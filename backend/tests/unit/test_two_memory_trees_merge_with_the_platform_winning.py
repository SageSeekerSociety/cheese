"""会话里那棵树和平台那一份怎么合成一份：三方，一条规矩。

跑在会话机上（runner）和跑在后端的是同一段代码，所以这里问的就是两端的答案。三
方是平台这一份、会话里那一份、上次铺下去的那一份；判据是指纹。一条规矩——**平台
赢了冲突**——其余的都从它推出来，所以这些用例其实是一张真值表。
"""

import pytest

from app.domain.memory.files import digest
from app.domain.memory.tree import REMOVED, sync_tree

_A = "平台那一版\n"
_A_HASH = digest(_A)
_B = "会话改过的那一版\n"
_B_HASH = digest(_B)
_C = "会话接着又改的那一版\n"
_LATER = "平台后来改的那一版\n"

_TEAM = {"team": {"a.md": _A}}


def _sync(*, scopes=_TEAM, disk=None, baseline=None):
    return sync_tree(scopes=scopes, disk=disk or {}, baseline=baseline or {})


def test_a_memory_the_platform_has_and_the_session_never_had_is_laid_down():
    result = _sync()
    assert result.files == {"team/a.md": _A}
    assert result.refused == {}
    assert result.baseline == {"team/a.md": _A_HASH}


def test_the_same_memory_on_both_sides_is_settled_once():
    result = _sync(disk={"team/a.md": _A}, baseline={"team/a.md": _A_HASH})
    assert result.files == {"team/a.md": _A}
    assert result.refused == {}


def test_the_platform_changed_and_the_session_did_not_takes_the_platform():
    result = _sync(disk={"team/a.md": _B}, baseline={"team/a.md": _B_HASH})
    assert result.files == {"team/a.md": _A}
    assert result.refused == {}


def test_the_session_changed_and_the_platform_did_not_keeps_the_session():
    """那正是 agent 刚写下的记忆——回写就是为它。"""
    result = _sync(disk={"team/a.md": _B}, baseline={"team/a.md": _A_HASH})
    assert result.files == {"team/a.md": _B}
    assert result.baseline == {"team/a.md": _B_HASH}
    assert result.refused == {}


def test_both_changed_and_the_platform_wins_while_the_session_gets_its_back():
    result = _sync(
        scopes={"team": {"a.md": _LATER}},
        disk={"team/a.md": _C},
        baseline={"team/a.md": _A_HASH},
    )
    assert result.files == {"team/a.md": _LATER}
    assert result.refused == {"team/a.md": _C}
    assert result.baseline == {"team/a.md": digest(_LATER)}


def test_a_deletion_in_the_session_is_kept_while_the_platform_stands_still():
    result = _sync(disk={}, baseline={"team/a.md": _A_HASH})
    assert result.files == {}
    assert result.refused == {}


def test_a_deletion_the_platform_also_touched_is_refused_as_a_deletion():
    """空串是「删了」，不是「改成了空」——房间里那句话得说得出是哪一种。"""
    result = _sync(
        scopes={"team": {"a.md": _LATER}},
        disk={},
        baseline={"team/a.md": _A_HASH},
    )
    assert result.files == {"team/a.md": _LATER}
    assert result.refused == {"team/a.md": REMOVED}


def test_a_scope_the_platform_stopped_naming_is_reconciled_and_taken_away():
    """上一次铺下去、这一次平台没提的（有人把这棵树关了），也要按同一套规矩收回来。"""
    result = _sync(
        scopes={},
        disk={"private/alice/a.md": _A},
        baseline={"private/alice/a.md": _A_HASH},
    )
    assert result.files == {}
    assert result.refused == {}


def test_a_file_the_session_wrote_into_a_closed_tree_is_refused_not_silently_dropped():
    result = _sync(
        scopes={},
        disk={"private/alice/a.md": _C},
        baseline={"private/alice/a.md": _A_HASH},
    )
    assert result.files == {}
    assert result.refused == {"private/alice/a.md": _C}


def test_a_path_only_the_baseline_knows_is_not_a_file_that_should_exist():
    result = _sync(scopes={}, disk={}, baseline={"team/gone.md": _A_HASH})
    assert result.files == {}
    assert result.baseline == {}


@pytest.mark.parametrize("key", ["elsewhere/a.md", "team/sub/a.md", "a.md", ""])
def test_a_path_outside_the_managed_trees_is_never_touched(key):
    result = _sync(scopes={}, disk={key: "别的东西\n"}, baseline={})
    assert result.files == {}
    assert result.baseline == {}


def test_the_baseline_comes_back_so_the_next_sync_can_tell_who_moved():
    """没有 baseline 就没有三方合并：两个都动了的时候，「谁改的」只能靠它答。"""
    first = _sync()
    second = sync_tree(
        scopes={"team": {"a.md": _LATER}},
        disk={"team/a.md": _A},
        baseline=first.baseline,
    )
    assert second.files == {"team/a.md": _LATER}
    assert second.refused == {}
