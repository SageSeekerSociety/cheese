"""「我的设备」 reads every machine's owner login once, not once per machine.

``/connector/my/devices`` asked ``owner_login.status`` inside its device loop,
so a list of N self-hosted machines cost N reads of ``device_claude_login``.
It now asks ``owner_login.statuses`` once, before the loop, for the
self-hosted ids alone (a cloud machine says nothing about its owner's own
Claude Code), and looks each answer up by id.

Three things are pinned:

1. ``my_devices`` with two self-hosted machines and one cloud machine reads
   the logins once, asks about exactly the two self-hosted ids, gives each
   machine its own view, and leaves the cloud machine without one;
2. ``statuses`` answers that in one query and in the same shape ``status``
   gives — a machine it has not been asked about is absent, as ``status``
   answers None for it;
3. put the read back to one machine at a time and the counting rule in 1
   goes red.
"""

from __future__ import annotations

import ast
import inspect
import types
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from app.api.routes.connector import connections
from app.domain.agent.harness.claude_code import owner_login
from app.domain.device import claude_login
from app.domain.device.models import DeviceClaudeLoginRow
from app.domain.device.supply import Supply

SELF_HOSTED = ("dev-a", "dev-b")
CLOUD = "cloud-c"


def _row(device_id: str) -> DeviceClaudeLoginRow:
    return DeviceClaudeLoginRow(
        device_id=device_id,
        installed=True,
        logged_in=True,
        auth_method="claude.ai",
        subscription_type="max",
        model="claude-opus-5-5",
        service_host=None,
        name=None,
        checked_at=datetime(2026, 10, 18, 8, 0, tzinfo=UTC),
        revoked_at=None,
    )


# The cloud machine has a row too: nothing may ask for it, and nothing may
# hand its login to a cloud machine's view.
ROWS = {device_id: _row(device_id) for device_id in (*SELF_HOSTED, CLOUD)}


class _Hub:
    def all_online_screens(self) -> list:
        return []

    def is_online(self, device_id: str) -> bool:
        return True

    def target(self, device_id: str) -> str:
        return "linux-x64"


class _Resolver:
    async def resolve(self):
        return types.SimpleNamespace(authenticated=True, user_id=7)


def _device(device_id: str, supply: Supply) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        device_id=device_id,
        name=device_id,
        team_id=7,
        last_seen_at=None,
        supply=supply,
    )


class _Service:
    async def list_enrolled_by(self, user_id: int):
        return [_device(device_id, Supply.self_hosted) for device_id in SELF_HOSTED] + [
            _device(CLOUD, Supply.cloud)
        ]

    async def sandbox_check(self, device_id: str):
        return None


class _Logins:
    """Stands in for ``owner_login``: the rows it holds and what it was asked."""

    def __init__(self, rows: dict[str, DeviceClaudeLoginRow]) -> None:
        self.rows = rows
        self.reads: list[list[str]] = []

    async def statuses(self, session, device_ids) -> dict[str, Any]:
        self.reads.append(list(device_ids))
        return {
            device_id: claude_login.view(self.rows[device_id])
            for device_id in device_ids
            if device_id in self.rows
        }

    async def status(self, session, device_id) -> dict[str, Any] | None:
        self.reads.append([device_id])
        row = self.rows.get(device_id)
        return None if row is None else claude_login.view(row)


class _OneMachineAtATime(_Logins):
    """The read this change replaced: one ``status`` per machine in the loop."""

    async def statuses(self, session, device_ids) -> dict[str, Any]:
        return {
            device_id: await self.status(session, device_id) for device_id in device_ids
        }


def _the_logins_were_read_once(logins: _Logins, out: dict[str, Any]) -> None:
    """One read for the whole list, of the self-hosted ids alone; and every
    self-hosted machine's view is what ``status`` would have said for it."""
    assert logins.reads == [list(SELF_HOSTED)]
    views = {view["device_id"]: view for view in out["devices"]}
    for device_id in SELF_HOSTED:
        assert views[device_id]["claude_code"] == claude_login.view(ROWS[device_id])
    assert "claude_code" not in views[CLOUD]


async def _listing(monkeypatch, logins: _Logins) -> dict[str, Any]:
    monkeypatch.setattr(connections, "owner_login", logins)
    monkeypatch.setattr(connections, "device_hub", _Hub())
    return await connections.my_devices(
        cast(Any, _Resolver()), cast(Any, _Service()), db=cast(Any, None)
    )


async def test_listing_devices_reads_the_logins_once(monkeypatch) -> None:
    logins = _Logins(ROWS)

    out = await _listing(monkeypatch, logins)

    _the_logins_were_read_once(logins, out)


async def test_going_back_to_one_status_per_machine_turns_the_rule_red(
    monkeypatch,
) -> None:
    """Plant the read this change replaced. The answers stay the same, so the
    red is the extra reads and nothing else.

    This guards ``_the_logins_were_read_once`` itself: delete its counting
    assertion and this test goes green. It is not a regression guard for the
    route — the count test above is the one that fails without the change.
    """
    logins = _OneMachineAtATime(ROWS)

    out = await _listing(monkeypatch, logins)

    views = {view["device_id"]: view for view in out["devices"]}
    for device_id in SELF_HOSTED:
        assert views[device_id]["claude_code"] == claude_login.view(ROWS[device_id])
    assert logins.reads == [[device_id] for device_id in SELF_HOSTED]
    with pytest.raises(AssertionError):
        _the_logins_were_read_once(logins, out)


class _ScalarRows:
    def __init__(self, rows) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _Result:
    def __init__(self, rows) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarRows:
        return _ScalarRows(self._rows)


def _ids_asked(statement) -> list[str]:
    """The ids an ``IN (...)`` carries, read from the statement's bound
    parameters rather than from one spelling of the expression: a rewrite that
    passes a tuple or a set still reads."""
    for value in statement.compile().params.values():
        if isinstance(value, (list, tuple, set, frozenset)):
            return [str(device_id) for device_id in value]
    return []


class _CountingSession:
    """The login table, counting the queries it answers."""

    def __init__(self, rows) -> None:
        self.rows = {row.device_id: row for row in rows}
        self.executes = 0
        self.gets = 0
        self.asked: list[str] = []

    async def execute(self, statement) -> _Result:
        self.executes += 1
        wanted = _ids_asked(statement)
        self.asked.extend(wanted)
        return _Result([row for d, row in self.rows.items() if d in wanted])

    async def get(self, _model, device_id, **_kwargs):
        self.gets += 1
        return self.rows.get(device_id)


async def test_statuses_answers_every_row_in_one_query() -> None:
    session = _CountingSession(ROWS.values())
    wanted = [*SELF_HOSTED, CLOUD]

    got = await owner_login.statuses(session, wanted)

    assert session.executes == 1
    assert session.gets == 0
    assert sorted(session.asked) == sorted(wanted)
    assert got == {
        device_id: claude_login.view(ROWS[device_id]) for device_id in wanted
    }


async def test_statuses_leaves_out_a_machine_it_has_not_asked_yet() -> None:
    session = _CountingSession(ROWS.values())

    got = await owner_login.statuses(session, [*SELF_HOSTED, "never-asked"])

    assert set(got) == set(SELF_HOSTED)
    assert session.executes == 1


async def test_statuses_asks_nothing_for_an_empty_list() -> None:
    session = _CountingSession(ROWS.values())

    assert await owner_login.statuses(session, []) == {}
    assert session.executes == 0


def _function(name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    tree = ast.parse(inspect.getsource(getattr(connections, name)))
    (node,) = tree.body
    assert isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    return node


def _calls(node: ast.AST) -> list[str]:
    out = []
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            func = call.func
            if isinstance(func, ast.Name):
                out.append(func.id)
            elif isinstance(func, ast.Attribute):
                base = func.value
                out.append(
                    f"{base.id}.{func.attr}"
                    if isinstance(base, ast.Name)
                    else func.attr
                )
    return out


_LOOPS = (
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def test_my_devices_reads_the_batch_outside_the_loop() -> None:
    fn = _function("my_devices")
    assert _calls(fn).count("owner_login.statuses") == 1
    assert "owner_login.status" not in _calls(fn)
    for loop in (n for n in ast.walk(fn) if isinstance(n, _LOOPS)):
        assert "owner_login.statuses" not in _calls(loop)
        assert "owner_login.status" not in _calls(loop)
