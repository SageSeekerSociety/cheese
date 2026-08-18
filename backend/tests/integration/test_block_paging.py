"""Cursor paging on the topic timeline (`GET /topics/{id}/blocks`).

The endpoint used to return every block a topic ever had — 2.1 MB / 2226 rows on
a real topic, which is what wedged the browser. Paging is opt-in: no `limit`
still means "the whole timeline", because agents read this endpoint to review
history and a default window would truncate them silently.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository


def _topic(client) -> str:
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    t = client.post("/topics", json={"project_id": p["id"], "title": "T"}).json()[
        "data"
    ]
    return t["id"]


def _say(client, tid: str, text: str) -> str:
    """Append one timeline block and return its id."""
    r = client.post(f"/topics/{tid}/decision", json={"decision": text})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _blocks(client, tid: str, **params):
    r = client.get(f"/topics/{tid}/blocks", params=params)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _texts(payload) -> list[str]:
    return [b["content"] for b in payload["data"]]


# --- the default must not change (agent compatibility) -----------------------


def test_no_limit_still_returns_the_whole_timeline(client):
    tid = _topic(client)
    for i in range(12):
        _say(client, tid, f"m{i}")

    payload = _blocks(client, tid)

    assert _texts(payload) == [f"m{i}" for i in range(12)]
    assert payload["total"] == 12
    # Nothing above the window, because there is no window.
    assert payload["has_more"] is False


# --- the window itself -------------------------------------------------------


def test_limit_returns_the_newest_n_oldest_first(client):
    tid = _topic(client)
    for i in range(12):
        _say(client, tid, f"m{i}")

    payload = _blocks(client, tid, limit=5)

    # Chat is bottom-anchored: a "page" is the TAIL, rendered oldest-first.
    assert _texts(payload) == ["m7", "m8", "m9", "m10", "m11"]
    # total stays the full conversation length, so the UI can say "of 12".
    assert payload["total"] == 12
    assert payload["has_more"] is True
    assert payload["oldest_id"] == payload["data"][0]["id"]


def test_walking_before_partitions_the_timeline_exactly(client):
    tid = _topic(client)
    for i in range(12):
        _say(client, tid, f"m{i}")

    seen: list[str] = []
    payload = _blocks(client, tid, limit=5)
    seen = _texts(payload) + seen
    pages = 1
    while payload["has_more"]:
        payload = _blocks(client, tid, limit=5, before=payload["oldest_id"])
        seen = _texts(payload) + seen
        pages += 1

    # No gaps, no repeats — the pages reassemble the timeline exactly.
    assert seen == [f"m{i}" for i in range(12)]
    assert pages == 3
    assert payload["has_more"] is False


def test_last_page_reports_no_more(client):
    tid = _topic(client)
    for i in range(4):
        _say(client, tid, f"m{i}")

    payload = _blocks(client, tid, limit=10)

    assert _texts(payload) == ["m0", "m1", "m2", "m3"]
    assert payload["has_more"] is False


def test_empty_topic_pages_cleanly(client):
    tid = _topic(client)

    payload = _blocks(client, tid, limit=20)

    assert payload["data"] == []
    assert payload["total"] == 0
    assert payload["has_more"] is False
    assert payload["oldest_id"] is None


# --- why a cursor and not an offset ------------------------------------------


def test_messages_arriving_at_the_tail_do_not_shift_the_next_page(client):
    """The whole reason this is a cursor: you scroll up while the chat grows."""
    tid = _topic(client)
    for i in range(10):
        _say(client, tid, f"m{i}")

    first = _blocks(client, tid, limit=5)
    assert _texts(first) == ["m5", "m6", "m7", "m8", "m9"]

    # Three replies land while the user is reading history.
    for i in range(10, 13):
        _say(client, tid, f"m{i}")

    older = _blocks(client, tid, limit=5, before=first["oldest_id"])

    # An offset window would have slid down by three and re-served m2..m6.
    assert _texts(older) == ["m0", "m1", "m2", "m3", "m4"]
    assert older["has_more"] is False
    # total does reflect the new arrivals — it is the live conversation length.
    assert older["total"] == 13


def test_blocks_sharing_a_timestamp_are_neither_skipped_nor_repeated(client):
    """created_at is stamped in Python, so a burst of streamed blocks can tie.

    Ordering on (created_at, id) keeps the cursor a total order; ordering on
    created_at alone would let tied rows fall through the crack between pages.
    """
    tid = _topic(client)
    pid = client.get(f"/topics/{tid}").json()["data"]["project_id"]
    stamp = datetime.now(UTC)

    async def seed() -> None:
        async with client.test_factory() as session:
            for i in range(9):
                # Three timestamps, three tied blocks each.
                session.add(
                    Block(
                        id=uuid.uuid4(),
                        project_id=uuid.UUID(pid),
                        topic_id=uuid.UUID(tid),
                        author="cheese",
                        author_type=AuthorType.ai,
                        content=f"b{i}",
                        kind=BlockKind.message,
                        created_at=stamp + timedelta(milliseconds=i // 3),
                        updated_at=stamp,
                    )
                )
            await session.commit()

    asyncio.run(seed())

    full = _texts(_blocks(client, tid))
    assert len(full) == 9

    seen: list[str] = []
    payload = _blocks(client, tid, limit=2)
    seen = _texts(payload) + seen
    while payload["has_more"]:
        payload = _blocks(client, tid, limit=2, before=payload["oldest_id"])
        seen = _texts(payload) + seen

    assert seen == full


# --- reactions must be scoped to the page too --------------------------------


def test_reactions_are_only_queried_for_the_current_page(client, monkeypatch):
    """Paging that still batch-queries reactions for all 2226 rows saves bytes
    and no database work. Record what the route actually asks for."""
    tid = _topic(client)
    ids = [_say(client, tid, f"m{i}") for i in range(12)]

    asked: list[list[str]] = []
    real = BlockRepository.reactions_for_blocks

    async def spy(self, block_ids):
        asked.append([str(b) for b in block_ids])
        return await real(self, block_ids)

    monkeypatch.setattr(BlockRepository, "reactions_for_blocks", spy)

    payload = _blocks(client, tid, limit=4)

    assert asked == [ids[-4:]]
    assert len(payload["data"]) == 4


def test_reactions_on_the_page_still_ride_the_payload(client):
    tid = _topic(client)
    ids = [_say(client, tid, f"m{i}") for i in range(6)]
    r = client.post(
        f"/blocks/{ids[-1]}/reactions", json={"emoji": "👍", "author": "u1"}
    )
    assert r.status_code == 200, r.text
    # …and one on a block that the page will NOT contain.
    client.post(f"/blocks/{ids[0]}/reactions", json={"emoji": "🎉", "author": "u1"})

    payload = _blocks(client, tid, limit=2)

    assert _texts(payload) == ["m4", "m5"]
    assert payload["data"][-1]["reactions"] == [
        {"emoji": "👍", "count": 1, "authors": ["u1"]}
    ]
    assert all("🎉" not in str(b.get("reactions", "")) for b in payload["data"])


# --- bad input ---------------------------------------------------------------


def test_unknown_cursor_is_rejected_not_silently_ignored(client):
    tid = _topic(client)
    _say(client, tid, "m0")

    r = client.get(
        f"/topics/{tid}/blocks",
        params={"limit": 5, "before": str(uuid.uuid4())},
    )

    # Falling back to "newest N" would hand back a duplicate page the caller
    # cannot tell apart from real older history.
    assert r.status_code == 404


def test_cursor_from_another_topic_is_rejected(client):
    a = _topic(client)
    b = _topic(client)
    _say(client, a, "a0")
    foreign = _say(client, b, "b0")

    r = client.get(f"/topics/{a}/blocks", params={"limit": 5, "before": foreign})

    assert r.status_code == 404


def test_limit_must_be_positive(client):
    tid = _topic(client)

    r = client.get(f"/topics/{tid}/blocks", params={"limit": 0})

    assert r.status_code == 400
