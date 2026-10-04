"""The runner's per-entry owner stamping (FB-56, P1-3): the owner moves at
the consumed native user entry, never at the send — so a predecessor's
entries stay its own, and a send nothing consumed leaves no mark."""

import asyncio
import json
import sys
import uuid
from pathlib import Path

import pytest

from app.domain.agent.harness.driven.runner import SessionStart
from app.domain.agent.harness.pi.journal import Journal
from app.domain.agent.harness.pi.runner import Runner, socket_path
from tests.support.room_machine import NO_MACHINE

FAKE = Path(__file__).resolve().parents[1] / "support/fake_pi.py"


def _entry(entry_id, role, text, parent=None):
    return {
        "type": "message",
        "id": entry_id,
        "parentId": parent,
        "timestamp": "2026-10-01T00:00:00.000Z",
        "message": {
            "role": role,
            "content": [{"type": "text", "text": text}],
            **({"stopReason": "stop"} if role == "assistant" else {}),
        },
    }


def _fixture(tmp_path: Path, stream: list[dict]) -> str:
    path = tmp_path / "recording.json"
    path.write_text(json.dumps({"why": "FB-56 stamping", "stream": stream}))
    shim = tmp_path / "pi"
    shim.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} {path} "$@"\n')
    shim.chmod(0o700)
    return str(shim)


async def _call(state: Path, method: str, params: dict | None = None) -> dict:
    reader, writer = await asyncio.open_unix_connection(socket_path(state))
    try:
        writer.write(
            json.dumps({"method": method, "params": params or {}}).encode() + b"\n"
        )
        await writer.drain()
        answer = json.loads(await reader.readline())
    finally:
        writer.close()
        await writer.wait_closed()
    if "error" in answer:
        raise RuntimeError(answer["error"])
    return answer["result"]


async def _entries(state: Path) -> list[dict]:
    return (await _call(state, "entries"))["entries"]


def _stamps(log: list[dict]) -> dict[str, str | None]:
    return {
        record["id"]: (record.get("cheese") or {}).get("work_id")
        for record in log
        if record.get("type") == "message"
    }


async def _send(state: Path, text: str, work_id: str) -> None:
    await _call(
        state,
        "send",
        {"input_id": str(uuid.uuid4()), "text": text, "work_id": work_id},
    )
    for _ in range(300):
        if not (await _call(state, "ping"))["working"]:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("turn never settled")


async def _start(tmp_path: Path, shim: str) -> Runner:
    runner = Runner(tmp_path / "state")
    await runner.start(
        SessionStart("system prompt", None, agent_handle="teammate"),
        binary=shim,
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin"},
        args=[],
        target=NO_MACHINE,
    )
    return runner


@pytest.mark.anyio
async def test_the_owner_moves_at_the_consumed_entry_not_the_send(tmp_path):
    """[S user, S output, T user, T output]: the send of T alone does not
    move the owner — S's entries are stamped S; only when T's user entry
    lands do T's entries become T's, and the durable owner follows."""
    nonce_s = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    nonce_t = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    shim = _fixture(
        tmp_path,
        [
            {"entry": _entry("u1", "user", f"先干活 {nonce_s}")},
            {"entry": _entry("a1", "assistant", "做好了", parent="u1")},
            {"pause": True},
            {"entry": _entry("u2", "user", f"接着做 {nonce_t}", parent="a1")},
            {"entry": _entry("a2", "assistant", "也做好了", parent="u2")},
            {"pause": True},
        ],
    )
    runner = await _start(tmp_path, shim)
    try:
        await _send(runner.state, f"先干活 {nonce_s}", "work-S")
        await _send(runner.state, f"接着做 {nonce_t}", "work-T")
        log = await _entries(runner.state)
        stamps = _stamps(log)
        assert stamps["u1"] == "work-S"
        assert stamps["a1"] == "work-S", "E 前的输出归 S"
        assert stamps["u2"] == "work-T"
        assert stamps["a2"] == "work-T", "E 后的输出归 T"
        journal = Journal(runner.state / "entries.sqlite")
        try:
            owner = json.loads(journal.recall("owner") or "{}")
        finally:
            journal.close()
        assert owner.get("work_id") == "work-T", (
            "持久 owner 在观察到 T 的 entry 后才推进"
        )
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_send_nothing_consumed_leaves_no_mark(tmp_path):
    """A send whose prompt never becomes an entry (the session was busy and
    refused it) moves nothing: every entry stays the predecessor's, the
    durable owner stays the predecessor's, and the refused work's id never
    appears."""
    nonce_s = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    nonce_x = f"⟪w:{uuid.uuid4().hex[:24]}⟫"
    shim = _fixture(
        tmp_path,
        [
            {"entry": _entry("u1", "user", f"先干活 {nonce_s}")},
            {"entry": _entry("a1", "assistant", "做到一半", parent="u1")},
            {"pause": True},
            # The refused prompt is not here — only S's own finish.
            {"entry": _entry("a2", "assistant", "做完了", parent="a1")},
            {"pause": True},
        ],
    )
    runner = await _start(tmp_path, shim)
    try:
        await _send(runner.state, f"先干活 {nonce_s}", "work-S")
        # The second send is accepted by the fake but never consumed: no
        # entry of it ever lands.
        await _send(runner.state, f"插队 {nonce_x}", "work-X")
        log = await _entries(runner.state)
        stamps = _stamps(log)
        assert set(stamps.values()) == {"work-S"}, "未被消费的 send 不留任何痕迹"
        journal = Journal(runner.state / "entries.sqlite")
        try:
            owner = json.loads(journal.recall("owner") or "{}")
        finally:
            journal.close()
        assert owner.get("work_id") == "work-S", "持久 owner 不被发送动作推进"
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_the_owner_and_the_page_and_the_cursor_move_together(tmp_path):
    """A page interrupted mid-import takes the computed owner and the cursor
    down with it: a fresh connection still sees the OLD owner, the OLD
    cursor and none of the page, and the replay then lands all three in one
    commit. The production INSERT OR IGNORE keeps its idempotence — the
    interruption here is a TEMP trigger on the fixture database, not a
    change of policy."""
    import json

    journal = Journal(tmp_path / "entries.sqlite")
    journal.remember("owner", json.dumps({"work_id": "work-S"}))
    journal.remember("received", "e0")
    good = [
        {
            "type": "message",
            "id": "u1",
            "parentId": None,
            "timestamp": "t",
            "message": {"role": "user", "content": [{"type": "text", "text": "先干"}]},
        },
        {
            "type": "message",
            "id": "sentinel",
            "parentId": "u1",
            "timestamp": "t2",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "中断点"}],
            },
        },
        {
            "type": "message",
            "id": "a1",
            "parentId": "sentinel",
            "timestamp": "t3",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "好"}],
            },
        },
    ]
    journal.connection.execute(
        "CREATE TEMP TRIGGER fail_page BEFORE INSERT ON entries "
        "WHEN NEW.id='sentinel' "
        "BEGIN SELECT RAISE(ABORT,'page interrupted'); END"
    )
    try:
        journal.import_entries(good, owner=json.dumps({"work_id": "work-T"}))
        raise AssertionError("the sentinel must interrupt the page")
    except Exception as exc:
        assert "page interrupted" in str(exc)
    journal.connection.execute("DROP TRIGGER IF EXISTS fail_page")

    # A FRESH connection is the witness: nothing of the page, the new owner
    # or the new cursor leaked.
    journal.close()
    fresh = Journal(tmp_path / "entries.sqlite")
    assert json.loads(fresh.recall("owner") or "{}").get("work_id") == "work-S", (
        "page 未落，owner 不推进"
    )
    assert fresh.recall("received") == "e0", "page 未落，cursor 不推进"
    assert fresh.read(0) == [], "page 未落，整页回滚"

    fresh.import_entries(good, owner=json.dumps({"work_id": "work-T"}))
    assert json.loads(fresh.recall("owner") or "{}").get("work_id") == "work-T"
    assert fresh.recall("received") == "a1"
    assert [row["record"]["id"] for row in fresh.read(0)] == ["u1", "sentinel", "a1"]
    fresh.close()
