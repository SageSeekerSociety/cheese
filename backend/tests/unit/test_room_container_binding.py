"""容器按房间分配 — the host-side half: which box does a topic's compute live in,
and what does "release this topic" mean once a box is shared.

The workspace layer answers both questions from `docker port` lookups on the
request path, so it is sync and holds no DB session; the provider (which does)
records the topic→room mapping for it. What matters here is that every failure
mode of that record degrades to the ONE-BOX-PER-TOPIC answer — a wrong room
would point 运行环境预览 at a stranger's container, while "unknown" merely means
"no box found", which is what an un-started topic should say anyway.
"""

import subprocess
import uuid

import pytest

from app.core.config import settings
from app.domain.workspace import service as ws


@pytest.fixture
def _rooms(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_share_room_container", True)
    return tmp_path


def test_an_unbound_topic_is_its_own_room(_rooms):
    """The fallback, and the reason it is the safe one: a topic that has never
    started a box gets its own name back, which reproduces the pre-room
    behaviour instead of guessing at someone else's box."""
    topic = uuid.uuid4()
    assert ws.room_for_topic(topic) == topic


def test_a_bound_topic_resolves_to_its_rooms_box(_rooms):
    room, task = uuid.uuid4(), uuid.uuid4()
    ws.bind_room(task, room)

    assert ws.room_for_topic(task) == room
    assert ws.tmux_container_name(ws.room_for_topic(task)) == ws.tmux_container_name(
        room
    )
    # Re-binding the same pair is a no-op, and rebinding to a new room wins
    # (a topic can only ever be in one).
    ws.bind_room(task, room)
    assert ws.room_for_topic(task) == room
    other = uuid.uuid4()
    ws.bind_room(task, other)
    assert ws.room_for_topic(task) == other


def test_a_corrupt_binding_degrades_to_the_topics_own_box(_rooms):
    """A half-written or hand-edited marker must not be able to send a topic
    into another room's container."""
    task = uuid.uuid4()
    ws.bind_room(task, uuid.uuid4())
    (_rooms / ".rooms" / task.hex).write_text("not-a-uuid")

    assert ws.room_for_topic(task) == task


def test_the_escape_hatch_puts_every_topic_back_in_its_own_box(_rooms, monkeypatch):
    """Sharing merges a room's fault domain — one topic OOMing the box takes its
    siblings with it. An operator hitting that needs a way out that does not
    require a code change, and it has to win over any binding already on disk."""
    room, task = uuid.uuid4(), uuid.uuid4()
    ws.bind_room(task, room)
    monkeypatch.setattr(settings, "sandbox_share_room_container", False)

    assert ws.room_for_topic(task) == task


def test_forget_room_is_idempotent(_rooms):
    task = uuid.uuid4()
    ws.forget_room(task)  # never bound — must not raise
    ws.bind_room(task, uuid.uuid4())
    ws.forget_room(task)
    assert ws.room_for_topic(task) == task


def _record_docker(monkeypatch) -> list[list[str]]:
    calls: list[list[str]] = []

    class _Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def _run(argv, **_kwargs):
        calls.append(list(argv))
        return _Done()

    monkeypatch.setattr(subprocess, "run", _run)
    return calls


def test_releasing_a_task_kills_its_session_and_spares_the_rooms_box(
    _rooms, monkeypatch
):
    """验收 #1's other half. Archiving one task must not stop the room's other
    topics: the box is named after the ROOM, so `docker rm` on the task's own
    name is a no-op by construction, and the thing that actually has to go is
    its tmux session."""
    monkeypatch.setattr(ws, "sandbox_available", lambda: True)
    monkeypatch.setattr(
        "app.domain.agent.hooks_substrate.schedule_topic_subscription_drop",
        lambda _t: None,
    )
    room, task = uuid.uuid4(), uuid.uuid4()
    ws.bind_room(task, room)
    calls = _record_docker(monkeypatch)

    ws.stop_topic_container(task)

    killed = [c for c in calls if "kill-session" in c]
    assert killed, "the task's session outlived its topic"
    assert killed[0][2] == ws.tmux_container_name(room)
    assert killed[0][-1] == ws.tmux_session_name(task)
    removed = {c[-1] for c in calls if c[:3] == ["docker", "rm", "-f"]}
    assert ws.tmux_container_name(room) not in removed, "the room's box was destroyed"
    # The binding goes with it — a released topic must not keep pointing at a
    # box it no longer has a session in.
    assert ws.room_for_topic(task) == task


def test_releasing_a_room_removes_the_box(_rooms, monkeypatch):
    """Archiving the ROOM is the case where taking its siblings down IS the
    intent — that is what archiving a room means."""
    monkeypatch.setattr(ws, "sandbox_available", lambda: True)
    monkeypatch.setattr(
        "app.domain.agent.hooks_substrate.schedule_topic_subscription_drop",
        lambda _t: None,
    )
    room = uuid.uuid4()
    ws.bind_room(room, room)
    calls = _record_docker(monkeypatch)

    ws.stop_topic_container(room)

    assert not [c for c in calls if "kill-session" in c], (
        "killing one session is pointless when the whole box is going"
    )
    removed = {c[-1] for c in calls if c[:3] == ["docker", "rm", "-f"]}
    assert ws.tmux_container_name(room) in removed
    assert ws.container_name(room) in removed, "the SDK box is still per topic"


def test_port_slots_are_distinct_and_never_collapse_to_zero(monkeypatch):
    """The block a room's box publishes. Slot 0 must keep the conventional
    ports, so a box that predates slots (and every lone topic) still answers
    on :3000."""
    assert ws.app_port_for_slot(0) == ws.APP_PORT
    assert ws.ttyd_port_for_slot(0) == ws.TTYD_PORT
    ports = {ws.app_port_for_slot(s) for s in range(ws.port_slots())}
    ports |= {ws.ttyd_port_for_slot(s) for s in range(ws.port_slots())}
    assert len(ports) == 2 * ws.port_slots(), "two slots share a published port"
    # A misconfigured zero would leave every box with no preview at all.
    monkeypatch.setattr(settings, "sandbox_room_port_slots", 0)
    assert ws.port_slots() == 1


def test_a_topics_session_name_is_its_own(_rooms):
    """Two topics in one box must never resolve to the same tmux session — that
    is one agent typing into another's claude."""
    a, b = uuid.uuid4(), uuid.uuid4()
    assert ws.tmux_session_name(a) != ws.tmux_session_name(b)
    # Sessions are discovered by prefix after a restart, so the legacy name has
    # to remain a prefix of the new scheme or recovery silently finds nothing.
    assert ws.tmux_session_name(a).startswith(ws.LEGACY_TMUX_SESSION)


def test_the_slot_lookup_never_prefix_matches_a_siblings_session(_rooms, monkeypatch):
    """`tmux -t <name>` falls back to PREFIX matching, and the legacy session
    name ``cheese`` is a prefix of every per-topic name — so a lookup written
    against the legacy name would hand a topic with no live session its
    SIBLING's preview port. Missing is fine; wrong is not."""
    asked: list[list[str]] = []

    class _Missing:
        returncode = 1
        stdout = ""
        stderr = "session not found"

    def _run(argv, **_kwargs):
        asked.append(list(argv))
        return _Missing()

    monkeypatch.setattr(subprocess, "run", _run)
    room, task = uuid.uuid4(), uuid.uuid4()
    ws.bind_room(task, room)

    assert ws.topic_port_slot(task) == 0
    targets = [c[c.index("-t") + 1] for c in asked if "-t" in c]
    assert targets == [ws.tmux_session_name(task)]
    assert ws.LEGACY_TMUX_SESSION not in targets
