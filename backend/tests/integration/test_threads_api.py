"""Contract tests for the human-facing /threads routes via TestClient."""

from datetime import UTC, datetime

import pytest
from anyio.from_thread import BlockingPortal
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.repositories import ProjectRepository
from tests.integration.conftest import CreatedUser


class TestThreadsApi:
    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ):
        self.client = api_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.db = db_session
        self.portal = _portal

    def _make_project(self, leader_id: int) -> int:
        async def _run():
            now = datetime.now(UTC)
            project = await ProjectRepository(self.db).create_project(
                name="p",
                description="d",
                color_code="#ffffff",
                team_id=1,
                leader_id=leader_id,
                start_date=now,
                end_date=now,
            )
            return project.id

        return self.portal.call(_run)

    def test_conversation_flow_as_leader(self):
        project_id = self._make_project(self.user.user_id)
        # create a thread
        resp = self.client.post(
            "/threads", headers=self.headers, json={"projectId": project_id, "title": "chat"}
        )
        assert resp.status_code == 201
        thread_id = resp.json()["data"]["thread"]["id"]

        # post a message as the human
        resp = self.client.post(
            f"/threads/{thread_id}/messages",
            headers=self.headers,
            json={"content": "hello team"},
        )
        assert resp.status_code == 201
        block = resp.json()["data"]["block"]
        assert block["content"] == "hello team"
        assert block["authorId"] == self.user.user_id
        assert block["threadId"] == thread_id

        # read the thread's messages
        resp = self.client.get(f"/threads/{thread_id}/messages", headers=self.headers)
        assert resp.status_code == 200
        contents = [b["content"] for b in resp.json()["data"]["blocks"]]
        assert contents == ["hello team"]

        # list project threads
        resp = self.client.get("/threads", headers=self.headers, params={"projectId": project_id})
        assert resp.status_code == 200
        assert [t["id"] for t in resp.json()["data"]["threads"]] == [thread_id]

    def test_pagination_hasmore_is_exact(self):
        project_id = self._make_project(self.user.user_id)
        resp = self.client.post(
            "/threads", headers=self.headers, json={"projectId": project_id}
        )
        thread_id = resp.json()["data"]["thread"]["id"]
        for i in range(3):
            self.client.post(
                f"/threads/{thread_id}/messages",
                headers=self.headers,
                json={"content": f"m{i}"},
            )

        # exact boundary: 3 messages, pageSize=3 -> hasMore must be False
        resp = self.client.get(
            f"/threads/{thread_id}/messages", headers=self.headers, params={"pageSize": 3}
        )
        page = resp.json()["data"]["page"]
        assert page["hasMore"] is False
        assert page["nextStart"] is None

        # pageSize=2 -> hasMore True, then the next page has the last one
        resp = self.client.get(
            f"/threads/{thread_id}/messages", headers=self.headers, params={"pageSize": 2}
        )
        data = resp.json()["data"]
        assert len(data["blocks"]) == 2
        assert data["page"]["hasMore"] is True
        next_start = data["page"]["nextStart"]

        resp = self.client.get(
            f"/threads/{thread_id}/messages",
            headers=self.headers,
            params={"pageSize": 2, "pageStart": next_start},
        )
        data = resp.json()["data"]
        assert len(data["blocks"]) == 1
        assert data["page"]["hasMore"] is False

    def test_non_member_forbidden(self):
        # a project whose leader is someone else, and the user has no membership
        project_id = self._make_project(self.user.user_id + 9_999_999)
        resp = self.client.post(
            "/threads", headers=self.headers, json={"projectId": project_id, "title": "x"}
        )
        assert resp.status_code == 403

    def test_unauthenticated_rejected(self):
        project_id = self._make_project(self.user.user_id)
        resp = self.client.post("/threads", json={"projectId": project_id})
        assert resp.status_code == 401
