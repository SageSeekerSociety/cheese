"""一次对账改了什么、这句话说给谁听。

同一份改动要说进两间房：team 的说进项目总览，某个人的 private 只说进他的私聊。所
以改动按路径分段存着，谁要哪一段自己取——把一个人的 private diff 说进总览，等于把
他的偏好广播给整个项目。空改动一个字都不说。
"""

import re

from app.domain.agent.platform_notices import memory_changed_notice
from app.domain.block.models import AGENT_NOTICE_META_KEY
from app.domain.memory.reads import MEMORY_DIR_MARKER
from app.domain.memory.session import DIFF_MAX_LINES, MemoryChange


def _change() -> MemoryChange:
    return MemoryChange(
        added=("team/b.md", "private/alice/p.md"),
        updated=("team/a.md",),
        removed=("private/alice/gone.md",),
        conflicted=("private/bob/c.md",),
        refused={"team/a.md": "会话那一版\n"},
        diffs={
            "team/a.md": "--- team/a.md\n+++ team/a.md\n-旧\n+新\n",
            "team/b.md": "--- team/b.md\n+++ team/b.md\n+新条目\n",
            "private/alice/p.md": "--- private/alice/p.md\n+++ ...\n+偏好\n",
        },
    )


def test_a_change_says_what_happened_in_one_line():
    assert _change().summary() == (
        "新增 2 条、修改 1 条、删除 1 条、1 条被别人抢先改了"
    )


def test_nothing_changed_says_nothing():
    empty = MemoryChange()
    assert empty.is_empty()
    assert empty.summary() == ""
    assert empty.diff == ""


def test_the_scope_it_is_said_in_lifts_out_only_its_own_tree():
    team = _change().scoped("team")
    assert team.added == ("team/b.md",)
    assert team.updated == ("team/a.md",)
    assert team.removed == ()
    assert team.conflicted == ()
    assert set(team.diffs) == {"team/a.md", "team/b.md"}

    alice = _change().scoped("private/alice")
    assert alice.added == ("private/alice/p.md",)
    assert alice.removed == ("private/alice/gone.md",)
    assert alice.conflicted == ()
    # 别人的 refused 一句都不能跟着出去：那是另一间房的事。
    assert alice.refused == {}
    assert set(alice.diffs) == {"private/alice/p.md"}


def test_a_scope_with_nothing_in_it_is_an_empty_change():
    assert _change().scoped("private/nobody").is_empty()


def test_the_diff_is_a_prefix_of_its_own_paths_in_order():
    diff = _change().scoped("team").diff
    assert diff.startswith("--- team/a.md")
    assert diff.index("team/a.md") < diff.index("team/b.md")
    assert "private/alice" not in diff


def test_a_huge_diff_is_clamped_to_the_room_event_budget():
    """索引能有几千行，一条事件不该跟着变成没人展开的附件——截断并说清还有多少。"""
    many = MemoryChange(diffs={"team/a.md": "".join(f"+第{i}行\n" for i in range(500))})
    lines = many.diff.splitlines()
    assert len(lines) == DIFF_MAX_LINES + 1
    assert lines[-1].startswith("…")


# --- 被平台盖回去的那一版，也要说给 agent 听 ------------------------------


def _notice(refused: dict[str, str]):
    return memory_changed_notice(
        where="项目共享",
        summary="新增 1 条、1 条被别人抢先改了",
        diff="--- team/a.md\n+++ team/a.md\n-旧\n+新\n",
        refused=refused,
    )


def _named(told: str) -> list[str]:
    """那句话里按条点名的路径（列表里反引号包着的那些）。"""
    return [
        path
        for line in told.splitlines()
        if line.startswith("- ")
        for path in re.findall(r"`([^`]+)`", line)
    ]


def test_a_refused_version_is_said_to_the_agent_and_not_only_to_the_room():
    """那条灰字是给人看的，而写记忆的 agent 在会话机上——它下一轮带进 prompt 的
    只有 `agent_notice`。不说，它会以为写成功了，下一轮再写一遍同一版。"""
    _, meta = _notice({"team/a.md": "会话那一版\n"})

    told = meta[AGENT_NOTICE_META_KEY]
    assert "~/.cheese/memory/team/a.md" in told
    # 它写的那一版还在旁边：说出路径，下一步才是 Read 它、把内容取回来。
    assert "~/.cheese/memory/team/a.conflict.md" in told
    assert "重读" in told


def test_the_paths_it_hands_over_are_ones_the_memory_tree_answers():
    """它下一步要拿这些路径去 Read。agent 的文件工具只把带 `.cheese/memory/`
    的路径发给会话机（`remote_execution/proxy.js` 的 `memoryPath`），其余的在
    **工作机**上找相对路径——那里没有记忆树，读回来是「文件不存在」。"""
    _, meta = _notice({"team/a.md": "会话那一版\n"})

    named = _named(meta[AGENT_NOTICE_META_KEY])
    assert named
    for path in named:
        assert MEMORY_DIR_MARKER in path, path


def test_a_refused_deletion_does_not_name_a_copy_that_was_never_written():
    """会话把这条删了、平台那之后也动过，平台这一版赢。会话那一版没有正文，会话
    机上不落这个旁路文件（`runner._keep_refused` 跳过空内容），所以那句话不能指
    一个副本给它——读到的永远是「文件不存在」。"""
    _, meta = _notice({"team/a.md": ""})

    told = meta[AGENT_NOTICE_META_KEY]
    assert _named(told) == ["~/.cheese/memory/team/a.md"]
    assert "没有副本" in told


def test_an_ordinary_change_carries_nothing_for_the_agent():
    """别的改动不需要它做任何事：一条 agent_notice 会跟着下一轮的 prompt 进去，
    白说的那句是每一轮都要付的。"""
    _, meta = _notice({})

    assert AGENT_NOTICE_META_KEY not in meta
