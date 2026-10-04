"""会话机上那一半：基线住在树里，被拒的那一版住在旁边。

`tree.sync_tree` 是两端共用的规矩（见
`test_two_memory_trees_merge_with_the_platform_winning.py`），而另外两件事只有会
话机上有：基线落在哪儿、被平台盖回去的那一版落在哪儿。两件都算错过，而且错得一样安静——基线放在树外面的时候，会话的家被重
建过一次以后，磁盘是空树、表还是满的，于是每一次对账都把平台上真实的记忆读成「会
话删光了整棵树」，照删不误，而删除没有历史可以恢复。
"""

import json
import shutil
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code.runner import (
    MEMORY_BASELINE,
    Runner,
    _recall_baseline,
    _write_memory,
    memory_root,
)
from app.domain.memory.files import MemoryFileError, check_scoped_path, digest

_A = "平台那一版\n"
_C = "会话改过的那一版\n"
_LATER = "平台后来改的那一版\n"
_NAMES = ("a", "b", "c", "d", "e")


def _session() -> Runner:
    """只够跑 `sync_memory` 的一个会话。

    那段代码用得到的就是 `launch`（日志那一行）和它自己的两个方法，它不碰进程、
    也不碰 journal，所以这里不要一个真的 runner。
    """
    session = object.__new__(Runner)
    session.launch = "test"
    return session


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把会话的家搬到 `tmp_path`：`memory_root()` 认的是 `$HOME`。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    return memory_root()


def _scopes(*names: str) -> dict:
    return {"project": {f"{name}.md": f"# {name}\n" for name in names}}


def _after_a_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Runner, Path, dict]:
    """摆一次真发生过的冲突，交回 (会话, 树根, 这次对账的结果)。

    会话把 `project/a.md` 改了，而平台那一版在那之后也变了——两边都动过，按规矩平台
    赢，会话那一版被带回来。
    """
    root = _home(tmp_path, monkeypatch)
    session = _session()
    session.sync_memory({"scopes": {"project": {"a.md": _A}}})
    (root / "project" / "a.md").write_text(_C, encoding="utf-8")
    outcome = session.sync_memory({"scopes": {"project": {"a.md": _LATER}}})
    assert outcome["refused"] == {"project/a.md": _C}
    return session, root, outcome


# --- 基线：和树同生同死 ---------------------------------------------------


def test_the_baseline_lives_in_the_tree_and_dies_with_it(tmp_path, monkeypatch):
    root = _home(tmp_path, monkeypatch)

    _session().sync_memory({"scopes": {"project": {"a.md": _A}}})

    assert _recall_baseline(root) == {"project/a.md": digest(_A)}
    shutil.rmtree(root)
    assert _recall_baseline(root) == {}


def test_a_rebuilt_home_is_a_new_session_not_one_that_deleted_everything(
    tmp_path, monkeypatch
):
    """家里那棵树和基线一起没了 —— 这是「新会话」，不是「会话把树删光了」。

    `resource_cleanup` 删 home 就是这么删的，而 journal 活得过家的重建。基线要是
    在树外面（进程里、另一台机器上），这里读到的就是「磁盘上一条都没有、上次却有
    五条」，五条平台上的记忆跟着消失。
    """
    root = _home(tmp_path, monkeypatch)
    scopes = _scopes(*_NAMES)
    laid_down = _session().sync_memory({"scopes": scopes})
    assert set(laid_down["files"]) == {f"project/{name}.md" for name in _NAMES}

    shutil.rmtree(root)

    again = _session().sync_memory({"scopes": scopes})
    assert again["files"] == laid_down["files"]
    assert again["refused"] == {}
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*.md")) == [
        f"project/{name}.md" for name in _NAMES
    ]


def test_a_baseline_left_behind_by_a_rebuilt_home_cannot_delete_the_tree(
    tmp_path, monkeypatch
):
    """上一条堵的是根因，这一条是兜底。

    万一盘上真的留下了一张满基线、树却是空的（旧版本就是这么留的），一次对账照样
    不能把平台上的记忆删光：整棵树的删除会被保险拦下，平台这一版重新铺下来。
    """
    root = _home(tmp_path, monkeypatch)
    root.mkdir(parents=True)
    _write_memory(
        root,
        MEMORY_BASELINE,
        json.dumps({f"project/{name}.md": digest(f"# {name}\n") for name in _NAMES}),
    )

    outcome = _session().sync_memory({"scopes": _scopes(*_NAMES)})

    assert outcome["files"] == {f"project/{name}.md": f"# {name}\n" for name in _NAMES}


# --- 被拒的那一版：留在它旁边 ---------------------------------------------


def test_the_version_the_platform_overwrote_is_left_next_to_it(tmp_path, monkeypatch):
    """房间那句话只说得出「有改动被盖回来了」，而那句话要落到 agent 手里它才能重
    读再写——它读到的是什么，取决于它还能不能看到自己刚写的那一版。"""
    _, root, _ = _after_a_conflict(tmp_path, monkeypatch)

    assert (root / "project" / "a.md").read_text(encoding="utf-8") == _LATER
    assert (root / "project" / "a.conflict.md").read_text(encoding="utf-8") == _C


def test_the_sidecar_is_not_a_memory_and_never_goes_back(tmp_path, monkeypatch):
    """它只是给写的人重读的一份旁路文件：不进树、不进索引、也不回平台。"""
    session, root, outcome = _after_a_conflict(tmp_path, monkeypatch)

    assert session.read_memory({"project"}) == ({"project/a.md": _LATER}, set())
    assert outcome["files"] == {"project/a.md": _LATER}
    # 回写那一侧同样过不去：写入端按路径对号入座，而这个路径先得过这一关。
    with pytest.raises(MemoryFileError):
        check_scoped_path("project/a.conflict.md")


def test_a_deletion_the_platform_overwrote_leaves_no_sidecar(tmp_path, monkeypatch):
    """删除没有正文可以留，而那句话本身已经把「你删的那条被平台留下了」说完——
    留一个空文件只会让 agent Read 到一个空文件。"""
    root = _home(tmp_path, monkeypatch)
    session = _session()
    session.sync_memory({"scopes": {"project": {"a.md": _A}}})
    (root / "project" / "a.md").unlink()

    outcome = session.sync_memory({"scopes": {"project": {"a.md": _LATER}}})

    assert outcome["refused"] == {"project/a.md": ""}
    assert (root / "project" / "a.md").read_text(encoding="utf-8") == _LATER
    assert not (root / "project" / "a.conflict.md").exists()


# --- 拦下来的那一批：跟着结果一起回去 -------------------------------------


def test_a_bulk_delete_from_the_session_comes_back_as_held(tmp_path, monkeypatch):
    """一个作用域一次少掉大半，会话机上只是拦下来（下一轮再铺回去），平台那一侧从
    `files` 里看不见这件事——所以它得跟着结果一起上去。整理那一轮正是拿它做判断：
    「这次删得太多」是它必须说出口的一句话，不是在会话机的 stderr 里响一声就完了。"""
    root = _home(tmp_path, monkeypatch)
    session = _session()
    scopes = _scopes(*_NAMES)
    session.sync_memory({"scopes": scopes})
    for name in _NAMES[:4]:
        (root / "project" / f"{name}.md").unlink()

    outcome = session.sync_memory({"scopes": scopes})

    assert outcome["held"] == [f"project/{name}.md" for name in _NAMES[:4]]
    assert outcome["files"] == {f"project/{name}.md": f"# {name}\n" for name in _NAMES}


def test_a_handful_of_deletions_is_not_held_and_goes_through(tmp_path, monkeypatch):
    """删一条是 agent 想明白了：不拦，也不回平台报。"""
    root = _home(tmp_path, monkeypatch)
    session = _session()
    session.sync_memory({"scopes": _scopes(*_NAMES)})
    (root / "project" / "a.md").unlink()

    outcome = session.sync_memory({"scopes": _scopes(*_NAMES)})

    assert outcome["held"] == []
    assert sorted(outcome["files"]) == [f"project/{name}.md" for name in _NAMES[1:]]


# --- 改名那一轮：会话机上的 team/ 搬成 project/ ---------------------------


def _old_team_tree(root: Path, content: str) -> None:
    """摆一台改名前的会话机：磁盘上一棵 `team/`，基线也是 `team/…` 为键。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "team").mkdir(parents=True, exist_ok=True)
    (root / "team" / "a.md").write_text(content, encoding="utf-8")
    _write_memory(root, MEMORY_BASELINE, json.dumps({"team/a.md": digest(_A)}))


def test_a_team_tree_survives_the_rename(tmp_path, monkeypatch):
    """改名这一轮不许把 `team/` 读成「会话删光了整棵树」而删掉。

    平台铺下来的已经从 `project/` 那一侧来，磁盘上却还躺着 `team/a.md`：前缀对
    不上号，三方合并里就没有一方认得它——搬一步（`_migrate_legacy_scope`）把树和
    基线一起改掉，这一条才对得上号、才活得下来。搬完 `team/` 一点不剩。
    """
    root = _home(tmp_path, monkeypatch)
    _old_team_tree(root, _A)

    outcome = _session().sync_memory({"scopes": {"project": {"a.md": _A}}})

    assert outcome["files"] == {"project/a.md": _A}
    assert (root / "project" / "a.md").read_text(encoding="utf-8") == _A
    assert not (root / "team").exists()
    assert _recall_baseline(root) == {"project/a.md": digest(_A)}


def test_an_edit_made_before_the_rename_is_not_lost(tmp_path, monkeypatch):
    """会话在改名之前改过的那一版，搬完照样是「会话改的」，回得去。

    基线指向的是平台铺过的那一版（`_A`），磁盘上却是会话改的（`_C`）——两边一比，
    会话那一版赢。要是基线的前缀没跟着搬，这一版就被当成凭空冒出来的文件，平台那
    一侧反而会把它盖回去。
    """
    root = _home(tmp_path, monkeypatch)
    _old_team_tree(root, _C)

    outcome = _session().sync_memory({"scopes": {"project": {"a.md": _A}}})

    assert outcome["files"] == {"project/a.md": _C}
    assert (root / "project" / "a.md").read_text(encoding="utf-8") == _C
    assert not (root / "team").exists()


def test_a_newer_platform_version_still_wins_after_the_rename(tmp_path, monkeypatch):
    """搬过去的两条对得上号之后，规矩不变：平台改过、会话没改，平台赢。"""
    root = _home(tmp_path, monkeypatch)
    _old_team_tree(root, _A)

    outcome = _session().sync_memory({"scopes": {"project": {"a.md": _LATER}}})

    assert outcome["files"] == {"project/a.md": _LATER}
    assert (root / "project" / "a.md").read_text(encoding="utf-8") == _LATER
    assert not (root / "team").exists()


def test_the_rename_does_not_clobber_what_the_platform_already_laid_down(
    tmp_path, monkeypatch
):
    """平台这一轮已经把更新的那一版铺进了 `project/a.md`，旧副本让位。

    「先搬树、再改基线」里的「不覆盖同名目标」就是为了这一刻：`project/` 那一份
    是平台刚写的、更新的，`team/` 那一份是上一轮残留的，谁新谁旧很清楚。
    """
    root = _home(tmp_path, monkeypatch)
    _old_team_tree(root, _A)
    (root / "project").mkdir(parents=True)
    (root / "project" / "a.md").write_text(_LATER, encoding="utf-8")

    outcome = _session().sync_memory({"scopes": {"project": {"a.md": _LATER}}})

    assert (root / "project" / "a.md").read_text(encoding="utf-8") == _LATER
    assert not (root / "team").exists()
    assert set(outcome["files"]) == {"project/a.md"}


def test_the_rename_runs_once_and_does_nothing_the_second_time(tmp_path, monkeypatch):
    """幂等：搬完 `team/` 就不在了，第二轮进来什么都不做，结果一模一样。"""
    root = _home(tmp_path, monkeypatch)
    _old_team_tree(root, _A)
    session = _session()

    first = session.sync_memory({"scopes": {"project": {"a.md": _A}}})
    second = session.sync_memory({"scopes": {"project": {"a.md": _A}}})

    assert first["files"] == second["files"] == {"project/a.md": _A}
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*.md")) == [
        "project/a.md"
    ]
