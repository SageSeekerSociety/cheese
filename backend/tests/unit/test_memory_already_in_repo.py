"""写进记忆之前，先问 repo 里是不是已经写着了（结论 61 后半）。

判据不是提示词里一句「别记 repo 里有的」——写入端每天要挡的正是它没照做的那几
次。所以这里跑的是真的检索结果：命中就拒绝，并且说得出命中在哪个文件的哪一行。
"""

import pytest

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
