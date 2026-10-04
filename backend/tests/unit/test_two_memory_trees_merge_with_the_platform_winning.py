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

_PROJECT = {"project": {"a.md": _A}}


def _sync(*, scopes=_PROJECT, disk=None, baseline=None):
    return sync_tree(scopes=scopes, disk=disk or {}, baseline=baseline or {})


def test_a_memory_the_platform_has_and_the_session_never_had_is_laid_down():
    result = _sync()
    assert result.files == {"project/a.md": _A}
    assert result.refused == {}
    assert result.baseline == {"project/a.md": _A_HASH}


def test_the_same_memory_on_both_sides_is_settled_once():
    result = _sync(disk={"project/a.md": _A}, baseline={"project/a.md": _A_HASH})
    assert result.files == {"project/a.md": _A}
    assert result.refused == {}


def test_the_platform_changed_and_the_session_did_not_takes_the_platform():
    result = _sync(disk={"project/a.md": _B}, baseline={"project/a.md": _B_HASH})
    assert result.files == {"project/a.md": _A}
    assert result.refused == {}


def test_the_session_changed_and_the_platform_did_not_keeps_the_session():
    """那正是 agent 刚写下的记忆——回写就是为它。"""
    result = _sync(disk={"project/a.md": _B}, baseline={"project/a.md": _A_HASH})
    assert result.files == {"project/a.md": _B}
    assert result.baseline == {"project/a.md": _B_HASH}
    assert result.refused == {}


def test_both_changed_and_the_platform_wins_while_the_session_gets_its_back():
    result = _sync(
        scopes={"project": {"a.md": _LATER}},
        disk={"project/a.md": _C},
        baseline={"project/a.md": _A_HASH},
    )
    assert result.files == {"project/a.md": _LATER}
    assert result.refused == {"project/a.md": _C}
    assert result.baseline == {"project/a.md": digest(_LATER)}


def test_a_deletion_in_the_session_is_kept_while_the_platform_stands_still():
    result = _sync(disk={}, baseline={"project/a.md": _A_HASH})
    assert result.files == {}
    assert result.refused == {}


def test_a_deletion_the_platform_also_touched_is_refused_as_a_deletion():
    """空串是「删了」，不是「改成了空」——房间里那句话得说得出是哪一种。"""
    result = _sync(
        scopes={"project": {"a.md": _LATER}},
        disk={},
        baseline={"project/a.md": _A_HASH},
    )
    assert result.files == {"project/a.md": _LATER}
    assert result.refused == {"project/a.md": REMOVED}


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
    result = _sync(scopes={}, disk={}, baseline={"project/gone.md": _A_HASH})
    assert result.files == {}
    assert result.baseline == {}


@pytest.mark.parametrize("key", ["elsewhere/a.md", "project/sub/a.md", "a.md", ""])
def test_a_path_outside_the_managed_trees_is_never_touched(key):
    result = _sync(scopes={}, disk={key: "别的东西\n"}, baseline={})
    assert result.files == {}
    assert result.baseline == {}


def test_the_baseline_comes_back_so_the_next_sync_can_tell_who_moved():
    """没有 baseline 就没有三方合并：两个都动了的时候，「谁改的」只能靠它答。"""
    first = _sync()
    second = sync_tree(
        scopes={"project": {"a.md": _LATER}},
        disk={"project/a.md": _A},
        baseline=first.baseline,
    )
    assert second.files == {"project/a.md": _LATER}
    assert second.refused == {}


# --- 保险：一次删掉半棵树，先当它没删（`BULK_DELETE_RATIO`/`BULK_DELETE_MIN`）--


def _paths(count: int, *, prefix: str = "project", stem: str = "m") -> list[str]:
    """一棵树里的 `count` 条路径。"""
    return [f"{prefix}/{stem}{i}.md" for i in range(count)]


def _names(paths: list[str]) -> dict[str, str]:
    """同一棵树在 `scopes` 里的样子：前缀在字典的键上，名字在这一层。"""
    return {path.rsplit("/", 1)[1]: _A for path in paths}


def test_a_tree_that_came_back_empty_is_not_a_tree_the_session_deleted():
    """空磁盘加上满基线，读出来的是「会话把整棵树删了」——而绝大多数时候那是会话
    的家被重建过。平台上的记忆跟着整批消失，且删除没有历史可以恢复，所以这一支由
    保险拦下：平台这一版重新铺下去，一条都不少。
    """
    paths = _paths(5)
    result = _sync(
        scopes={"project": _names(paths)},
        disk={},
        baseline={path: _A_HASH for path in paths},
    )
    assert result.files == {path: _A for path in paths}
    assert result.held == tuple(paths)
    assert result.refused == {}
    assert result.baseline == {path: _A_HASH for path in paths}


def test_a_minority_of_deletions_is_still_a_deletion():
    """保险拦的是「半棵树一起没了」：一个作用域里十条删掉四条，那是 agent 想明白
    了——它该删得掉，否则「一次删掉半棵树」就成了「谁都别删」。
    """
    paths = _paths(10)
    kept = paths[4:]
    result = _sync(
        scopes={"project": _names(paths)},
        disk={path: _A for path in kept},
        baseline={path: _A_HASH for path in paths},
    )
    assert result.files == {path: _A for path in kept}
    assert result.held == ()


def test_the_valve_counts_each_tree_on_its_own():
    """按作用域分开数：一个人清空了自己那棵 private，就是「整棵树没了」，哪怕项目
    那一棵好端端地在旁边替它分摊了比例——合起来数的话，这里恰好落在阈值下面，被删
    的那棵树就一点保护都没有了。
    """
    project = _paths(5, stem="t")
    alice = _paths(4, prefix="private/alice", stem="p")
    result = _sync(
        scopes={"project": _names(project), "private/alice": _names(alice)},
        # alice 那棵树整个没了，项目那一棵一条没动。
        disk={path: _A for path in project},
        baseline={path: _A_HASH for path in project + alice},
    )
    assert result.held == tuple(alice)
    assert result.files == {path: _A for path in project + alice}
