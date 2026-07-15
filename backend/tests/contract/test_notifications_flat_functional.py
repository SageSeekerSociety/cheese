"""Functional tests for the flat ``/notifications`` inbox.

The shape tests in ``test_notifications_contract.py`` only exercise the empty
inbox. These seed real ``Notification`` rows (via the client's isolated
per-worker ``test_factory``, on the same DB the app reads through the overridden
``get_db``) and drive the full read → mark-read → delete lifecycle plus cursor
pagination, so a regression in the wiring — not just the response envelope — is
caught.

All rows are addressed to the seeded platform agent user (id=1, "cheese") that
``authed_client`` authenticates as.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.domain.notification.models import Notification, NotificationType

_AGENT_USER_ID = 1


async def _seed(
    factory,
    *,
    read: bool = False,
    type_: NotificationType = NotificationType.MENTION,
    created_at: datetime | None = None,
    metadata: dict | None = None,
) -> int:
    now = datetime.now(UTC)
    async with factory() as session:
        row = Notification(
            receiver_id=_AGENT_USER_ID,
            type=type_,
            read=read,
            finalized=True,
            metadata_payload=metadata or {},
            created_at=created_at or now,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
        row_id = row.id
        await session.commit()
    return row_id


@pytest.mark.anyio
async def test_lifecycle_read_and_delete(authed_client: AsyncClient) -> None:
    factory = authed_client.test_factory  # type: ignore[attr-defined]
    id1 = await _seed(factory)
    id2 = await _seed(factory)

    # Two unread in the inbox.
    resp = await authed_client.get("/notifications/unread-count")
    assert resp.json()["data"]["count"] == 2

    resp = await authed_client.get("/notifications", params={"pageSize": 10})
    data = resp.json()["data"]
    assert {n["id"] for n in data["notifications"]} == {id1, id2}
    assert all(n["read"] is False for n in data["notifications"])
    assert data["page"]["total"] == 2
    assert data["page"]["hasMore"] is False

    # Mark one read → it flips, unread count drops.
    resp = await authed_client.patch(f"/notifications/{id1}", json={"read": True})
    assert resp.status_code == 200
    assert resp.json()["data"]["notification"]["read"] is True
    resp = await authed_client.get("/notifications/unread-count")
    assert resp.json()["data"]["count"] == 1

    # Collective mark-all-read clears the remaining one.
    resp = await authed_client.put("/notifications/status", json={"read": True})
    assert resp.json()["data"]["count"] == 1
    resp = await authed_client.get("/notifications/unread-count")
    assert resp.json()["data"]["count"] == 0

    # Delete → gone (204), then 404 on re-fetch; the sibling survives.
    resp = await authed_client.delete(f"/notifications/{id1}")
    assert resp.status_code == 204
    resp = await authed_client.get(f"/notifications/{id1}")
    assert resp.status_code == 404
    resp = await authed_client.get(f"/notifications/{id2}")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_cursor_pagination_round_trip(authed_client: AsyncClient) -> None:
    """Seed 3 rows with distinct timestamps and page through them 2-at-a-time,
    round-tripping the opaque ``nextStart`` cursor back into ``pageStart``."""
    factory = authed_client.test_factory  # type: ignore[attr-defined]
    base = datetime.now(UTC)
    # Newest-first ordering: newer created_at comes first.
    oldest = await _seed(factory, created_at=base - timedelta(minutes=3))
    middle = await _seed(factory, created_at=base - timedelta(minutes=2))
    newest = await _seed(factory, created_at=base - timedelta(minutes=1))

    resp = await authed_client.get("/notifications", params={"pageSize": 2})
    page1 = resp.json()["data"]
    assert [n["id"] for n in page1["notifications"]] == [newest, middle]
    assert page1["page"]["hasMore"] is True
    assert page1["page"]["total"] == 3
    cursor = page1["page"]["nextStart"]
    assert cursor

    resp = await authed_client.get(
        "/notifications", params={"pageSize": 2, "pageStart": cursor}
    )
    page2 = resp.json()["data"]
    assert [n["id"] for n in page2["notifications"]] == [oldest]
    assert page2["page"]["hasMore"] is False


@pytest.mark.anyio
async def test_read_filter_and_type_filter(authed_client: AsyncClient) -> None:
    factory = authed_client.test_factory  # type: ignore[attr-defined]
    await _seed(factory, read=False, type_=NotificationType.MENTION)
    await _seed(factory, read=True, type_=NotificationType.REPLY)

    # read=false filter → only the unread mention.
    resp = await authed_client.get(
        "/notifications", params={"pageSize": 10, "read": False}
    )
    data = resp.json()["data"]
    assert data["page"]["total"] == 1
    assert data["notifications"][0]["type"] == "MENTION"

    # type filter narrows to REPLY.
    resp = await authed_client.get(
        "/notifications", params={"pageSize": 10, "type": "REPLY"}
    )
    data = resp.json()["data"]
    assert data["page"]["total"] == 1
    assert data["notifications"][0]["type"] == "REPLY"

    # Unknown type → 400.
    resp = await authed_client.get(
        "/notifications", params={"pageSize": 10, "type": "NOPE"}
    )
    assert resp.status_code == 400


async def _seed_user_with_profile(factory, nickname: str) -> int:
    """Seed a User + UserProfile and return the user id, so a notification's
    metadata can reference a resolvable actor."""
    from app.domain.user.models import User, UserProfile

    now = datetime.now(UTC)
    async with factory() as session:
        user = User(
            username=f"actor-{nickname}",
            email=f"actor-{nickname}@example.com",
            hashed_password="x",
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()
        session.add(
            UserProfile(
                user_id=user.id,
                nickname=nickname,
                intro="",
                avatar_id=1,
                created_at=now,
                updated_at=now,
            )
        )
        user_id = user.id
        await session.commit()
    return user_id


@pytest.mark.anyio
async def test_entities_resolved_from_metadata(authed_client: AsyncClient) -> None:
    """A notification whose metadata references a user resolves to that user's
    display info through the wired resolvers."""
    factory = authed_client.test_factory  # type: ignore[attr-defined]
    actor_id = await _seed_user_with_profile(factory, "Mochi")
    await _seed(
        factory,
        metadata={"actor": {"type": "user", "id": str(actor_id)}},
    )

    resp = await authed_client.get("/notifications", params={"pageSize": 10})
    entities = resp.json()["data"]["notifications"][0]["entities"]
    assert "actor" in entities
    assert entities["actor"] is not None
    assert entities["actor"]["type"] == "user"
    assert entities["actor"]["id"] == str(actor_id)
    assert entities["actor"]["name"] == "Mochi"
