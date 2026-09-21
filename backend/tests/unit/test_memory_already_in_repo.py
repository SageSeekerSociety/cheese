"""写进记忆之前，先问 repo 里是不是已经写着了（结论 61 后半）。

判据不是提示词里一句「别记 repo 里有的」——写入端每天要挡的正是它没照做的那几
次。所以这里跑的是真的检索结果：命中就拒绝，并且说得出命中在哪个文件的哪一行。
"""

import subprocess
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code.remote_execution.runtime import Executor
from app.domain.memory.redundant import already_in_repo

pytestmark = pytest.mark.anyio


def _checkout(*lines: dict):
    async def search(terms: list[str]) -> list[dict]:
        assert terms, "没有关键词还去检索，等于把整个 repo 当成命中"
        return [
            line
            for line in lines
            if any(term.lower() in str(line["text"]).lower() for term in terms)
        ]

    return search


async def test_a_fact_already_written_in_a_file_is_found_with_its_path():
    hit = await already_in_repo(
        "前端构建用 pnpm，不要用 npm",
        _checkout(
            {
                "path": "docs/frontend.md",
                "line": 12,
                "text": "前端构建用 pnpm，不要用 npm。",
            }
        ),
    )
    assert hit is not None
    # 路径是这次拒绝的全部价值：没有它，调用方只能换个说法再写一遍。
    assert hit.path == "docs/frontend.md"
    assert hit.line == 12


async def test_the_file_that_says_it_best_is_the_one_named():
    """拒绝理由只带一个路径，带哪一个决定了对方接下来读的是不是那份文件。"""
    hit = await already_in_repo(
        "前端构建用 pnpm，不要用 npm",
        _checkout(
            {
                "path": "CHANGELOG.md",
                "line": 88,
                "text": "前端构建用 pnpm 了，不要用别的",
            },
            {
                "path": "docs/frontend.md",
                "line": 12,
                "text": "前端构建用 pnpm，不要用 npm。",
            },
        ),
    )
    assert hit is not None
    assert hit.path == "docs/frontend.md"


async def test_merely_sharing_a_word_is_not_the_same_fact():
    """只共用一个词不算。这个阈值比 `cheese recall` 严是有代价的一边倒：拒错了，
    那条事实就没被记下来，而记忆不可再生（结论 61）。"""
    hit = await already_in_repo(
        "王老师周三下午不看消息，有事提前一天问",
        _checkout(
            {"path": "README.md", "line": 3, "text": "周三 的 CI 跑得比较慢"},
        ),
    )
    assert hit is None


async def test_nothing_in_the_checkout_means_nothing_to_refuse():
    hit = await already_in_repo("他要结论在最前面", _checkout())
    assert hit is None


async def test_a_fact_with_no_usable_keyword_is_never_refused():
    """全是标点或停用词的时候没得可查——这时候拒绝就是凭空拒绝一次写入。"""
    calls = []

    async def search(terms: list[str]) -> list[dict]:
        calls.append(terms)
        return [{"path": "a.py", "line": 1, "text": "..."}]

    assert await already_in_repo("……", search) is None
    assert calls == [], "没有关键词就不该去打扰那台机器"


# --- 机器那一侧：真的在一个检出目录里检索一次 ----------------------------


def _executor(root: Path) -> Executor:
    """一个只够回答 `repo_search` 的执行器——它用到的就是这两样。"""
    executor = object.__new__(Executor)
    executor.config = {}
    executor.root = root
    return executor


def _a_checkout(root: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_the_line_stating_the_fact_survives_a_repo_full_of_one_keyword(tmp_path):
    """判据只在真仓库里才有用，而真仓库里单个关键词到处都是。

    「前端构建用 pnpm，不要用 npm」里的 `npm`，在一个前端仓里几百个文件都有。每个
    关键词各算一次命中的话，窗口在路径序靠前的目录里就用完了，真正写着这件事的那
    一行根本排不进来——判据在它唯一该生效的场景里静默失效。
    """
    noise = {
        f"a/pkg{i}/package.json": '{"scripts": {"build": "npm run build"}}\n'
        for i in range(400)
    }
    checkout = _a_checkout(
        tmp_path / "web",
        {**noise, "docs/frontend.md": "# 前端\n\n前端构建用 pnpm，不要用 npm。\n"},
    )

    found = _executor(checkout).repo_search({"terms": ["前端构建用", "不要用", "npm"]})

    assert found["searched"] is True
    assert [hit["path"] for hit in found["hits"]] == ["docs/frontend.md"]
    assert found["hits"][0]["line"] == 3


def test_a_checkout_that_does_not_carry_the_fact_says_so(tmp_path):
    checkout = _a_checkout(tmp_path / "web", {"README.md": "CI 在 Actions 上跑\n"})

    found = _executor(checkout).repo_search({"terms": ["王老师", "周三下午"]})

    assert found == {"searched": True, "hits": []}


def test_a_search_that_could_not_run_is_not_an_empty_repo(tmp_path):
    """「repo 里确实没写」和「这次没查成」是相反的两个答案。

    分不开的话，一台机器上的检索从此一条都拦不住，而外面一个信号都没有：写入端把
    它当成「repo 里没有」照单全收，而 P35 的验收正是靠这条判据。
    """
    loose = tmp_path / "loose"
    loose.mkdir()

    found = _executor(loose).repo_search({"terms": ["前端构建用"]})

    assert found["searched"] is False
    assert found["reason"] and found["hits"] == []
