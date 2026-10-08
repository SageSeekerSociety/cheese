"""``LiveWork``：这台进程此刻手上正接着的活，按房间、按轮次键住。

This is exactly the state that used to sit scattered over ``ChatService``
(``hook_work`` / ``active_turn_ids`` / the seat and topic locks / the note
tables). What is worth pinning is not the storage but the keys and what moves
together: one seat's turns queue on one lock while two seats in a room run
side by side; two live turns in one room are both remembered and each ends on
its own; and two services never see each other's work. These hold the real
object — the smallest layer that can be held, no database and no socket.
"""

import uuid
from datetime import UTC, datetime

from app.api import deps as session_turn_deps
from app.domain.agent.chat import ChatService
from app.domain.agent.live_work import HookWorkState, LiveWork
from tests.conftest import stub_compute


def _service() -> ChatService:
    return ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=None,
        base_system_prompt="You are Cheese.",
        workspace_root="/tmp/live-work-ws",
        compute=stub_compute(),
    )


def test_one_seat_shares_a_lock_and_two_seats_do_not():
    live = LiveWork()
    room, other = uuid.uuid4(), uuid.uuid4()
    # The same (room, seat) is the same lock, so that agent's turns queue…
    assert live.seat_lock_for(room, "cheese") is live.seat_lock_for(room, "cheese")
    # …while another seat in the room, or the same seat in another room, is
    # a different lock and runs beside it.
    assert live.seat_lock_for(room, "cheese") is not live.seat_lock_for(room, "other")
    assert live.seat_lock_for(room, "cheese") is not live.seat_lock_for(other, "cheese")


def test_the_room_lock_is_its_own_key_space():
    live = LiveWork()
    room = uuid.uuid4()
    assert live.lock_for(room) is live.lock_for(room)
    assert live.lock_for(room) is not live.lock_for(uuid.uuid4())
    # A room lock and a seat lock for the same room are different locks.
    assert live.lock_for(room) is not live.seat_lock_for(room, "cheese")


def _state(topic_id, work_id, **changes) -> HookWorkState:
    fields = dict(
        project_id=uuid.uuid4(),
        topic_id=topic_id,
        work_id=work_id,
        pending_ids=set(),
        reply_to=None,
        roster=[],
        topic_refs=[],
        continuation_id=None,
        route="native",
        acting_agent="cheese",
        agent_pool=None,
        user_text="",
        started_at=datetime.now(UTC),
        agent_instance_handle="inst",
    )
    fields.update(changes)
    return HookWorkState(**fields)


def test_two_live_turns_are_both_remembered_and_close_independently():
    live = LiveWork()
    topic = uuid.uuid4()
    a, b = uuid.uuid4(), uuid.uuid4()
    live.mark_turn_active(topic, a)
    live.mark_turn_active(topic, b)
    assert live.active_turn_ids[topic] == {a, b}

    # Closing one turn leaves the other live — no room-wide clear.
    live.mark_turn_inactive(topic, a)
    assert live.active_turn_ids[topic] == {b}
    live.mark_turn_inactive(topic, b)
    assert topic not in live.active_turn_ids  # the last one takes the room out


def test_consuming_work_id_does_not_misattribute_between_live_turns():
    live = LiveWork()
    topic = uuid.uuid4()
    a, b = uuid.uuid4(), uuid.uuid4()
    live.hook_work[(topic, a)] = _state(topic, a, agent_instance_handle="inst-a")
    live.hook_work[(topic, b)] = _state(topic, b, agent_instance_handle="inst-b")
    live.mark_turn_active(topic, a)
    live.mark_turn_active(topic, b)

    to = lambda handle: lambda s: s.agent_instance_handle == handle  # noqa: E731
    # Exactly one recipient matches: that turn is the one.
    assert live.consuming_work_id(topic, to("inst-a")) == a
    assert live.consuming_work_id(topic, to("inst-b")) == b
    # Two live turns and no exact match: held, never sent to a guessed turn.
    assert live.consuming_work_id(topic, lambda s: True) is None
    assert live.consuming_work_id(topic) is None

    # Closing one leaves the survivor the unambiguous target.
    live.mark_turn_inactive(topic, a)
    assert live.consuming_work_id(topic) == b


def test_two_services_do_not_share_the_work_they_are_holding():
    one, two = _service(), _service()
    assert one.live is not two.live
    topic = uuid.uuid4()
    one.live.mark_turn_active(topic, uuid.uuid4())
    assert one.live.active_turn_ids
    assert two.live.active_turn_ids == {}
