"""Read the session's durable output through the same API as its raw view."""

import time

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
