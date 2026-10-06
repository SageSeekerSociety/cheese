"""A notice about a turn says whose turn it was.

A room may seat several AI teammates. When the turn addressed to one of them
fails or waits, the room shows the line beside that teammate, so the line has
to name it — the room cannot tell otherwise, and guessing the first seat put
B's failure under A. The line itself stays the platform's: a machine's error
under a teammate's name would read as that teammate's judgement."""

import uuid
from datetime import UTC, datetime

from app.api.deps import get_chat_service
from app.domain.agent.platform_notices import (
    EVENT_TURN_FAILED,
    SEVERITY_ERROR,
    WHO_HUMAN,
    notice,
)
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.delivery.models import Delivery
from tests.integration.conftest import post_project, session_auth_headers

SECOND_SEAT = "cheese-0123456789ab"


def room(client):
    project = post_project(client, json={"name": "Seats"}, owner="alice").json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Work"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    return uuid.UUID(topic["id"])


def failure_meta():
    return notice(EVENT_TURN_FAILED, severity=SEVERITY_ERROR, who=WHO_HUMAN)


def timeline_line(client, topic, content):
    blocks = client.get(
        f"/topics/{topic}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return next(b for b in blocks if b["content"] == content)


def test_a_failed_turn_is_reported_under_the_seat_it_ran_on(client):
    topic = room(client)
    chat = client.app.dependency_overrides[get_chat_service]()
    turn = uuid.uuid4()

    async def fail_a_turn_of_the_second_seat():
        async with chat.session_factory() as db:
            await AgentTurnRepository(db).open(
                turn_id=turn,
                conversation_id=topic,
                continuation_id=turn,
                author="alice",
                content="@second 看一下",
                is_resume=False,
                resendable=False,
                started_at=datetime.now(UTC),
                agent_handle=SECOND_SEAT,
            )
            await db.commit()
        await chat.post_system_event(topic, "这一轮没能完成", turn, meta=failure_meta())

    client.portal.call(fail_a_turn_of_the_second_seat)
    line = timeline_line(client, topic, "这一轮没能完成")
    assert line["author"] == "system"
    assert line["meta"]["seat"] == SECOND_SEAT


def test_a_turn_waiting_before_it_starts_is_reported_under_its_addressed_seat(client):
    topic = room(client)
    chat = client.app.dependency_overrides[get_chat_service]()
    attempt = uuid.uuid4()

    async def queue_a_delivery_to_the_second_seat():
        now = datetime.now(UTC)
        event = uuid.uuid4()
        async with chat.session_factory() as db:
            db.add(
                Delivery(
                    event_id=event,
                    recipient_handle=SECOND_SEAT,
                    dedup_key=f"{event}:{SECOND_SEAT}",
                    type="MENTION",
                    payload={},
                    event_at=now,
                    recorded_at=now,
                    conversation_id=topic,
                    state="claimed",
                    attempt_id=attempt,
                )
            )
            await db.commit()
        # The turn this delivery starts is still waiting: it has no row yet.
        await chat.post_system_event(topic, "排队中", attempt, meta=failure_meta())

    client.portal.call(queue_a_delivery_to_the_second_seat)
    assert timeline_line(client, topic, "排队中")["meta"]["seat"] == SECOND_SEAT


def test_a_notice_whose_turn_has_no_seat_names_none(client):
    topic = room(client)
    chat = client.app.dependency_overrides[get_chat_service]()

    async def fail_an_unattributed_turn():
        await chat.post_system_event(
            topic, "平台出错了", uuid.uuid4(), meta=failure_meta()
        )

    client.portal.call(fail_an_unattributed_turn)
    assert "seat" not in timeline_line(client, topic, "平台出错了")["meta"]
