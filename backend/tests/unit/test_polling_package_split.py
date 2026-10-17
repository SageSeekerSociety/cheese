"""门面守卫：``app.domain.review.services.polling`` 由模块拆成包之后，名字一个都不能少。

拆之前它是一个 886 行的模块，按职责搬进 ``polling/`` 下的五个子模块，``__init__.py``
只做转出。调用方（``review/services/service.py`` 里逐个 ``polling.<名字>`` 的转发）、
按模块属性取名字的 patch 点、以及测试，都没改，靠的就是这个门面把同一批名字重新摊开。

这道测试不连数据库，只钉住三件事：

1. 拆之前那份模块自己定义的 25 个名字，门面一个不少地答得出来。
2. 每个名字在子模块里只有**一处**实现，门面自己一处都不定义，旧路径那份模块也不在
   了 —— 一个函数在两个文件里各留一份，是这种拆分最容易出的错，两边还都跑得通。
3. 按包属性 patch 仍然咬得住：``service.py`` 的方法体是在**调用时**才读
   ``polling.<名字>``，所以 ``monkeypatch.setattr(polling, ...)`` 改的仍是那条路。
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock

import app.domain.review.services.polling as polling
from app.domain.review.services import AcceptService

PACKAGE = Path(polling.__file__).resolve().parent
FACADE = "__init__.py"

#: 下面这五个是这一版拆出来的形状；门面没转出别的子模块。
PIECES = {"advance.py", "card_poll.py", "clients.py", "seen_head.py", "snapshot.py"}

#: 拆之前那份 `polling.py` 自己定义的名字，照搬运前那份文件抄的。
MODULE_NAMES = (
    "_seen_head",
    "_seen_head_or_refresh",
    "_refresh_never_shown_card",
    "_refresh_github_unseen_head",
    "_status_client",
    "_remote",
    "_refresh_stale_card",
    "_pr_poll_credentials",
    "_app_credentials",
    "advance_pr_card",
    "_advance_claimed",
    "refresh_stale_pr_snapshots",
    "_refreshing",
    "_snapshot_is_due",
    "_refresh_once",
    "_refresh_in_own_session",
    "_merge_snapshot_age_s",
    "_refresh_stale_pr_snapshot",
    "_refresh_github_snapshot",
    "_advance_github_card",
    "_mark_task_merged",
    "_app_pr_client",
    "note_poll_crashed",
    "_poll_pr_card",
    "_update_behind_branch",
)


def _defined_here(path: Path) -> set[str]:
    """这份文件在自己这一层定义的名字：函数、类、赋值（含带标注的赋值）。"""
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8"), str(path)).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _holders() -> dict[str, list[str]]:
    """每个名字落在哪几个子模块里。"""
    found = {name: [] for name in MODULE_NAMES}
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name == FACADE:
            continue
        for name in _defined_here(path):
            if name in found:
                found[name].append(path.name)
    return found


def test_the_facade_answers_every_name_the_module_defined() -> None:
    """名字少一个，`service.py` 那条转发就是 AttributeError，而这里先红。"""
    missing = [name for name in MODULE_NAMES if not hasattr(polling, name)]
    assert missing == []


def test_each_name_has_exactly_one_implementation() -> None:
    """一份实现，而且只有一份；门面不自己抄一份，旧模块也不还在。"""
    assert PIECES <= {path.name for path in PACKAGE.glob("*.py")}

    twice = {name: where for name, where in _holders().items() if len(where) != 1}
    assert twice == {}, f"这些名字不在恰好一个子模块里：{twice}"
    assert _defined_here(PACKAGE / FACADE) == set()
    assert not (PACKAGE.parent / "polling.py").exists()


async def test_a_patch_on_the_package_name_still_reaches_the_call(
    monkeypatch,
) -> None:
    """门面是调用时读的那个对象：这正是拆包保留 monkeypatch 兼容的地方。"""
    sentinel = AsyncMock()
    monkeypatch.setattr(polling, "refresh_stale_pr_snapshots", sentinel)

    service = AcceptService.__new__(AcceptService)
    await service.refresh_stale_pr_snapshots(cards=[])

    assert sentinel.await_count == 1
    assert sentinel.await_args.args[0] is service
