"""现场 reads a page without fetching the whole turn, and fetches it in parallel.

The rules, without the code: a page shows the newest records, so opening 现场
must not pull the beginning of a long turn; a sealed object never changes, so
reading the same page again must not fetch it again; and a page that spans
several turns must fetch them at the same time rather than one after another.
All three are what a reader waits for.
"""

import asyncio
import gzip
import json
import uuid

from app.domain.agent.harness import CLAUDE_CODE
from app.domain.session_record import service
from tests.integration.test_a_turns_raw_record_keeps_what_it_did import (
    _Bucket,
    _room_for,
    _seal,
    _Session,
)


def _long_turn(work: str, count: int) -> list[dict]:
    """One turn's journal: `count` steps, each naming itself."""
    return [
        {
            "type": "assistant",
            "uuid": str(uuid.uuid4()),
            "message": {
                "id": f"msg_{i}",
                "role": "assistant",
                "content": [{"type": "text", "text": f"step {i:04d}"}],
            },
            "parent_tool_use_id": None,
            "cheese": {"work_id": work},
        }
        for i in range(count)
    ]


class _CountingBucket(_Bucket):
    """The private bucket, in memory, keeping the bodies it was asked for."""

    def __init__(self) -> None:
        super().__init__()
        self.bodies: list[bytes] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def download(self, key: str) -> bytes | None:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            # Let the other fetches of the same page start before this one ends:
            # a caller that fetches them one by one never reaches two.
            await asyncio.sleep(0.02)
            body = self.objects.get(key)
            if body is not None:
                self.bodies.append(body)
            return body
        finally:
            self.in_flight -= 1


def _site(client, topic: uuid.UUID, *, limit: int = 120) -> dict:
    response = client.get(f"/topics/{topic}/transcript", params={"limit": limit})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _sealed(
    client, tmp_path, monkeypatch, count: int
) -> tuple[_CountingBucket, uuid.UUID]:
    bucket = _CountingBucket()
    monkeypatch.setattr(service, "private_storage", lambda: bucket)
    monkeypatch.setattr(service, "bucket", lambda: bucket)
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    session.write(_long_turn(str(uuid.uuid4()), count))
    # One drain takes one journal page (256 records); a turn longer than that
    # needs as many passes as it has pages.
    session.drain(pages=(count + 255) // 256)
    _seal(client, bucket)
    return bucket, session.ref.topic_id


def test_the_newest_page_does_not_fetch_the_beginning_of_a_long_turn(
    client, tmp_path, monkeypatch
):
    # A long turn: one turn, several sealed runs. The number is the test's own
    # (it does not read the seal cap), so changing the cap does not change what
    # the reader asked for — only how much of it a page has to fetch.
    bucket, topic = _sealed(client, tmp_path, monkeypatch, count=1200)
    bucket.bodies.clear()

    page = _site(client, topic)

    assert len(page["data"]) == 120
    bodies = [gzip.decompress(body).decode() for body in bucket.bodies]
    fetched = "".join(bodies)
    assert "step 1199" in fetched, "the page's own steps"
    # The page is the tail of the turn; its first steps are not on screen and
    # must not be fetched (gzip cannot be read from the middle).
    assert '"step 0000"' not in fetched


def test_reading_the_same_page_again_keeps_the_sealed_body(
    client, tmp_path, monkeypatch
):
    bucket, topic = _sealed(client, tmp_path, monkeypatch, count=900)
    bucket.bodies.clear()

    _site(client, topic)
    first = len(bucket.bodies)
    assert first > 0

    _site(client, topic)
    assert len(bucket.bodies) == first, "a sealed object is immutable; it was refetched"


def test_a_page_spanning_several_turns_fetches_them_together(
    client, tmp_path, monkeypatch
):
    bucket = _CountingBucket()
    monkeypatch.setattr(service, "private_storage", lambda: bucket)
    monkeypatch.setattr(service, "bucket", lambda: bucket)
    session = _Session(client, tmp_path, CLAUDE_CODE, _room_for(client))
    for _ in range(3):
        session.write(_long_turn(str(uuid.uuid4()), 60))
    session.drain()
    _seal(client, bucket)
    bucket.max_in_flight = 0

    page = _site(client, session.ref.topic_id)

    assert len(page["data"]) == 120
    assert bucket.max_in_flight > 1, "each turn's body was fetched after the last one"


def test_a_short_turn_still_needs_only_its_own_object(client, tmp_path, monkeypatch):
    bucket, topic = _sealed(client, tmp_path, monkeypatch, count=10)
    bucket.bodies.clear()

    page = _site(client, topic)

    assert len(page["data"]) == 10
    assert json.loads(gzip.decompress(bucket.bodies[0]).decode().splitlines()[0])[
        "record"
    ]["message"]["content"][0]["text"].startswith("step ")
