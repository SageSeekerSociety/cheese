from datetime import UTC, datetime

import pytest
from anyio.from_thread import BlockingPortal
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notification.models import Notification
from tests.integration.conftest import UserCreator


def create_notifications_in_db(
    db_session: AsyncSession,
    portal: BlockingPortal,
    receiver_id: int,
    count: int = 3,
    notification_type: str = "MENTION",
    read: bool = False,
) -> list[int]:
    """Insert notifications via ORM and return their ids."""
    # Notification.created_at / updated_at are naive `timestamp without time
    # zone` columns; pass a naive UTC datetime to match the schema.
    now = datetime.now(UTC)
    sample_content = {"actorId": 1, "targetType": "comment", "targetId": 123}

    notifications = [
        Notification(
            receiver_id=receiver_id,
            type=notification_type,
            read=read,
            created_at=now,
            updated_at=now,
            is_aggregatable=False,
            finalized=True,
            version=0,
            metadata_payload={},
            content=sample_content,
        )
        for _ in range(count)
    ]

    async def _do() -> list[int]:
        # Allocate ids from the notification_seq up-front so .id is populated
        # before flush (the ORM model wires the sequence in).
        for n in notifications:
            db_session.add(n)
        await db_session.flush()
        return [n.id for n in notifications]

    return portal.call(_do)


def delete_notifications_in_db(
    db_session: AsyncSession, portal: BlockingPortal, notification_ids: list[int]
) -> None:
    """No-op kept for backwards compatibility — the per-test transaction
    rollback wipes inserted rows automatically.

    A real DELETE is still issued so that within a single test you can verify
    rows are gone.
    """
    if not notification_ids:
        return

    async def _do() -> None:
        await db_session.execute(
            text("DELETE FROM notification WHERE id = ANY(:ids)"),
            {"ids": notification_ids},
        )
        await db_session.flush()

    portal.call(_do)


class TestNotificationIntegration:
    @pytest.fixture
    def setup_notifications(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        notification_ids = create_notifications_in_db(db_session, _portal, creator.user_id, count=3)

        yield {
            "creator": creator,
            "notification_ids": notification_ids,
        }

    def test_list_notifications(self, setup_notifications: dict, api_client: TestClient):
        creator = setup_notifications["creator"]

        resp = api_client.get(
            "/notifications",
            params={"pageSize": 10},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "notifications" in data
        assert "page" in data
        assert isinstance(data["notifications"], list)

    def test_list_notifications_with_type_and_read_filters(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ):
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        create_notifications_in_db(
            db_session, _portal, creator.user_id, count=2, notification_type="MENTION", read=False
        )
        create_notifications_in_db(
            db_session, _portal, creator.user_id, count=1, notification_type="MENTION", read=True
        )
        create_notifications_in_db(
            db_session, _portal, creator.user_id, count=1, notification_type="REPLY", read=False
        )

        resp = api_client.get(
            "/notifications",
            params={"pageSize": 20, "type": "MENTION", "read": "false"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        notifications = data["notifications"]
        assert len(notifications) == 2
        for n in notifications:
            assert n["type"] == "MENTION"
            assert n["read"] is False

        resp2 = api_client.get(
            "/notifications",
            params={"pageSize": 20, "type": "MENTION", "read": "true"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp2.status_code == 200
        data2 = resp2.json()["data"]
        assert len(data2["notifications"]) == 1
        assert data2["notifications"][0]["read"] is True

        resp3 = api_client.get(
            "/notifications",
            params={"pageSize": 20, "type": "REPLY"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp3.status_code == 200
        data3 = resp3.json()["data"]
        assert len(data3["notifications"]) == 1
        assert data3["notifications"][0]["type"] == "REPLY"

    def test_get_unread_count(self, setup_notifications: dict, api_client: TestClient):
        creator = setup_notifications["creator"]

        resp = api_client.get(
            "/notifications/unread-count",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "count" in data
        assert data["count"] >= 3

    def test_get_notification_by_id(self, setup_notifications: dict, api_client: TestClient):
        creator = setup_notifications["creator"]
        notification_ids = setup_notifications["notification_ids"]
        notification_id = notification_ids[0]

        resp = api_client.get(
            f"/notifications/{notification_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "notification" in data
        assert data["notification"]["id"] == notification_id

    def test_get_notification_not_found(self, setup_notifications: dict, api_client: TestClient):
        creator = setup_notifications["creator"]

        resp = api_client.get(
            "/notifications/999999999",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    def test_update_notification_status(self, setup_notifications: dict, api_client: TestClient):
        creator = setup_notifications["creator"]
        notification_ids = setup_notifications["notification_ids"]
        notification_id = notification_ids[0]

        resp = api_client.patch(
            f"/notifications/{notification_id}",
            json={"read": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert data["notification"]["read"] is True

        resp2 = api_client.patch(
            f"/notifications/{notification_id}",
            json={"read": False},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp2.status_code == 200, f"Expected 200, got {resp2.status_code}: {resp2.text}"
        data2 = resp2.json()["data"]
        assert data2["notification"]["read"] is False

    def test_update_notification_not_found(self, setup_notifications: dict, api_client: TestClient):
        creator = setup_notifications["creator"]

        resp = api_client.patch(
            "/notifications/999999999",
            json={"read": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    def test_bulk_update_notifications(self, setup_notifications: dict, api_client: TestClient):
        creator = setup_notifications["creator"]
        notification_ids = setup_notifications["notification_ids"]

        resp = api_client.patch(
            "/notifications",
            json={
                "updates": [
                    {"id": notification_ids[0], "read": True},
                    {"id": notification_ids[1], "read": True},
                ]
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "updatedIds" in data

    def test_mark_all_as_read(self, setup_notifications: dict, api_client: TestClient):
        creator = setup_notifications["creator"]

        resp = api_client.put(
            "/notifications/status",
            json={"read": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "count" in data

    def test_mark_all_as_read_requires_true(
        self, setup_notifications: dict, api_client: TestClient
    ):
        creator = setup_notifications["creator"]

        resp = api_client.put(
            "/notifications/status",
            json={"read": False},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

    def test_delete_notification(self, setup_notifications: dict, api_client: TestClient):
        creator = setup_notifications["creator"]
        notification_ids = setup_notifications["notification_ids"]
        notification_id = notification_ids[-1]

        resp = api_client.delete(
            f"/notifications/{notification_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

        resp2 = api_client.get(
            f"/notifications/{notification_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp2.status_code == 404, f"Expected 404 after delete, got {resp2.status_code}"

    def test_delete_notification_not_found(self, setup_notifications: dict, api_client: TestClient):
        creator = setup_notifications["creator"]

        resp = api_client.delete(
            "/notifications/999999999",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    def test_list_notifications_with_pagination(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ):
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        create_notifications_in_db(db_session, _portal, creator.user_id, count=5)

        resp = api_client.get(
            "/notifications",
            params={"pageSize": 2},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert len(data["notifications"]) <= 2
        assert "page" in data
        page = data["page"]
        if page.get("hasMore"):
            assert page.get("nextStart") is not None

    def test_list_notifications_empty(self, user_client: UserCreator, api_client: TestClient):
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        resp = api_client.get(
            "/notifications",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "notifications" in data
        assert isinstance(data["notifications"], list)
