"""Read the session's durable output through the same API as its raw view."""

import time
import uuid

from sqlalchemy import and_, or_, select

from app.domain.agent.harness import HARNESSES
from app.domain.delivery.models import NativeInput
from tests.integration.conftest import session_auth_headers
from tests.support.hang import HANG_S


def records(client, conversation, *, owner="alice") -> list[dict]:
    response = client.get(
        f"/topics/{conversation}/session-records",
        params={"limit": 500},
        headers=session_auth_headers(owner),
    )
    assert response.status_code == 200, response.text
    page = response.json()["data"]
    assert page["next"] is None, "the test must read every session record"
    return page["items"]


def wait_for_record(
    client, conversation, predicate, *, owner="alice", timeout=HANG_S
) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        found = records(client, conversation, owner=owner)
        for item in found:
            if predicate(item["record"]):
                return item
        assert time.monotonic() < deadline, found
        time.sleep(0.02)


def assistant_texts(items: list[dict]) -> list[str]:
    return [
        part["text"]
        for item in items
        if item["record"].get("type") == "assistant"
        for part in item["record"]["message"]["content"]
        if part.get("type") == "text"
    ]


def wait_for_read(client, conversation, *, timeout=HANG_S) -> None:
    """Until the session in ``conversation`` has read an input: its echo, for
    a harness whose runner reports reads, or its runner taking it, for one
    whose acceptance is the read. The records can land before the read does."""
    reporting = [n for n, h in HARNESSES.items() if h.reports_lifecycle]

    async def read() -> bool:
        async with client.test_factory() as session:
            found = await session.scalar(
                select(NativeInput.id).where(
                    NativeInput.conversation_id == uuid.UUID(str(conversation)),
                    or_(
                        NativeInput.echoed_at.is_not(None),
                        and_(
                            NativeInput.harness.not_in(reporting),
                            NativeInput.accepted_at.is_not(None),
                        ),
                    ),
                )
            )
            return found is not None

    deadline = time.monotonic() + timeout
    while not client.portal.call(read):
        assert time.monotonic() < deadline, "the session never read its input"
        time.sleep(0.05)
