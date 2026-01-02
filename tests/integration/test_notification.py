from __future__ import annotations

import random
from datetime import datetime, timezone

import httpx
import psycopg2
import pytest

from tests.integration.conftest import UserCreator


def create_notifications_in_db(
    receiver_id: int, count: int = 3, notification_type: str = "MENTION", read: bool = False
) -> list[int]:
    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        user="postgres",
        password="postgres",
        database="postgres",
    )
    notification_ids = []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM notification")
            start_id = cur.fetchone()[0]

            for i in range(count):
                nid = start_id + i
                now = datetime.now(timezone.utc)
                cur.execute(
                    """
                    INSERT INTO notification (id, receiver_id, type, read, created_at, updated_at, is_aggregatable, finalized, version, metadata, content)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        nid,
                        receiver_id,
                        notification_type,
                        read,
                        now,
                        now,
                        False,
                        True,
                        0,
                        "{}",
                        '{"actorId": 1, "targetType": "comment", "targetId": 123}',
                    ),
                )
                notification_ids.append(nid)
        conn.commit()
    finally:
        conn.close()
    return notification_ids


def delete_notifications_in_db(notification_ids: list[int]) -> None:
    if not notification_ids:
        return
    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        user="postgres",
        password="postgres",
        database="postgres",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM notification WHERE id = ANY(%s)",
                (notification_ids,),
            )
        conn.commit()
    finally:
        conn.close()


class TestNotificationIntegration:
    @pytest.fixture
    def setup_notifications(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        notification_ids = create_notifications_in_db(creator.user_id, count=3)

        yield {
            "creator": creator,
            "notification_ids": notification_ids,
        }

        delete_notifications_in_db(notification_ids)

    def test_list_notifications(self, setup_notifications: dict, api_client: httpx.Client):
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
        self, user_client: UserCreator, api_client: httpx.Client
    ):
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        mention_unread = create_notifications_in_db(
            creator.user_id, count=2, notification_type="MENTION", read=False
        )
        mention_read = create_notifications_in_db(
            creator.user_id, count=1, notification_type="MENTION", read=True
        )
        reply_unread = create_notifications_in_db(
            creator.user_id, count=1, notification_type="REPLY", read=False
        )
        all_ids = mention_unread + mention_read + reply_unread

        try:
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
        finally:
            delete_notifications_in_db(all_ids)

    def test_get_unread_count(self, setup_notifications: dict, api_client: httpx.Client):
        creator = setup_notifications["creator"]

        resp = api_client.get(
            "/notifications/unread-count",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "count" in data
        assert data["count"] >= 3

    def test_get_notification_by_id(self, setup_notifications: dict, api_client: httpx.Client):
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

    def test_get_notification_not_found(self, setup_notifications: dict, api_client: httpx.Client):
        creator = setup_notifications["creator"]

        resp = api_client.get(
            "/notifications/999999999",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    def test_update_notification_status(self, setup_notifications: dict, api_client: httpx.Client):
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

    def test_update_notification_not_found(self, setup_notifications: dict, api_client: httpx.Client):
        creator = setup_notifications["creator"]

        resp = api_client.patch(
            "/notifications/999999999",
            json={"read": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    def test_bulk_update_notifications(self, setup_notifications: dict, api_client: httpx.Client):
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

    def test_mark_all_as_read(self, setup_notifications: dict, api_client: httpx.Client):
        creator = setup_notifications["creator"]

        resp = api_client.put(
            "/notifications/status",
            json={"read": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]
        assert "count" in data

    def test_mark_all_as_read_requires_true(self, setup_notifications: dict, api_client: httpx.Client):
        creator = setup_notifications["creator"]

        resp = api_client.put(
            "/notifications/status",
            json={"read": False},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

    def test_delete_notification(self, setup_notifications: dict, api_client: httpx.Client):
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

    def test_delete_notification_not_found(self, setup_notifications: dict, api_client: httpx.Client):
        creator = setup_notifications["creator"]

        resp = api_client.delete(
            "/notifications/999999999",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    def test_list_notifications_with_pagination(
        self, user_client: UserCreator, api_client: httpx.Client
    ):
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        notification_ids = create_notifications_in_db(creator.user_id, count=5)

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

        delete_notifications_in_db(notification_ids)

    def test_list_notifications_empty(
        self, user_client: UserCreator, api_client: httpx.Client
    ):
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
