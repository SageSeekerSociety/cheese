"""lean 技能里那份给目标项目抄的 CI 模板，要真的是能用的、且盖住了那个坑。

模板不是本仓库的工作流——它随技能发到会话里，人复制到目标项目的
``.github/workflows/lean.yml``。所以没有任何 CI 会去跑它，写错了要到别人项目上
才发现。这里替它做两件事：

1. YAML 解得开、作业名是 ``lean``（平台的 ``required_checks`` 按这个 check 名找
   它，改名而不同步改那边就是静默失效）、action 钉在 40 位提交上。
2. ``references/ci.md`` 里给的那条 ``required_checks`` 路径例子，用平台**自己的**
   匹配器试一遍：根目录的 ``Foo.lean`` 也要命中。

第 2 条是这条测试存在的理由。平台的匹配器（``review/merge_state.py`` 的
``_glob_regex``）里 ``**`` 跨目录、``*`` 不跨目录，所以 ``**/*.lean`` 命中不了仓库
根目录的 ``Foo.lean``——只在子目录放证明的项目发现不了，根目录放一个就漏。把
``*.lean`` 从例子里删掉，这条会红。
"""

from __future__ import annotations

import json
import re

import yaml

from app.domain.review.merge_state import _glob_regex
from app.domain.skill_library import _NATIVE_SKILL_SRC

LEAN = _NATIVE_SKILL_SRC / "lean"
WORKFLOW = LEAN / "references" / "lean.yml"
CI_DOC = LEAN / "references" / "ci.md"


def _workflow() -> dict:
    # `on:` 被 YAML 1.1 读成布尔 True，取键时两种都认。
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _on(doc: dict) -> dict:
    return doc.get("on", doc.get(True))


def test_the_template_is_valid_yaml_and_the_job_is_named_lean():
    job = _workflow()["jobs"]["lean"]
    assert job["name"] == "lean", (
        "check run 名就是它，平台的 required_checks 按这个名字找"
    )


def test_the_action_is_pinned_to_a_commit():
    """标签可变：@v1 能被指到别的提交，review 过的东西第二天就不是那份了。"""
    uses = [s["uses"] for s in _workflow()["jobs"]["lean"]["steps"] if "uses" in s]
    pinned = [
        u
        for u in uses
        if re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", u.split(" #")[0].strip())
    ]
    assert pinned, f"没有钉到 40 位提交的 action：{uses}"
    assert any("lean-action" in u for u in pinned)


def test_both_path_filters_cover_a_root_level_lean_file():
    """GitHub 自己的路径过滤：`**/*.lean` 与 `*.lean` 一个都不能少。

    两边的语义不同（GitHub 的 `**/` 收零层目录，平台的不收），所以工作流里两条
    都写、required_checks 里也两条都写，是最省心的做法。"""
    on = _on(_workflow())
    for event in ("push", "pull_request"):
        paths = on[event]["paths"]
        assert "**/*.lean" in paths, f"{event} 少了 **/*.lean"
        assert "*.lean" in paths, f"{event} 少了 *.lean（根目录的证明漏掉）"


def test_the_required_checks_example_covers_a_root_level_file_under_our_matcher():
    """例子里的 paths 拿平台的匹配器试：根目录和子目录都要命中。

    删掉 `*.lean` 这一条，`Foo.lean` 就不再命中——那正是「检查静默地不生效」，
    在项目的 CI 上看不出任何异常。"""
    # 正文里另有一处占位写法 `{"name": "lean", "paths": [...]}`，所以要取
    # ```json 代码块里的那条真例子，别拿正则在整篇里瞎碰。
    blocks = re.findall(r"```json\n(.*?)\n```", CI_DOC.read_text("utf-8"), re.DOTALL)
    examples = [json.loads(b) for b in blocks if b.strip().startswith("{")]
    assert examples, "ci.md 里没有 required_checks 的 json 例子"
    paths = examples[0]["paths"]
    assert any(_glob_regex(p).match("Foo.lean") for p in paths), (
        f"{paths} 命中不了根目录的 Foo.lean"
    )
    assert any(_glob_regex(p).match("Sub/Foo.lean") for p in paths), (
        f"{paths} 命中不了子目录的 Sub/Foo.lean"
    )
