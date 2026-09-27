"""一次对账改了什么、这句话说给谁听。

同一份改动要说进两间房：team 的说进项目总览，某个人的 private 只说进他的私聊。所
以改动按路径分段存着，谁要哪一段自己取——把一个人的 private diff 说进总览，等于把
他的偏好广播给整个项目。空改动一个字都不说。
"""

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
