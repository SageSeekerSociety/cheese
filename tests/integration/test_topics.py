"""
Integration tests for the Topics module.
Migrated from cheese-backend/test/topic.e2e-spec.ts (361 lines, 15 tests)
Complete equivalence migration.
"""

import time

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import CreatedUser, UserCreator, unique_int


class TestTopicsCreateIntegration:
    """Tests for creating topics."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.topic_code = str(unique_int(1000000000, 9999999999))
        self.topic_prefix = f"[Test({self.topic_code}) Topic]"
        self.topic_ids: list[int] = []

    def test_create_topics(self):
        topics = [
            "高等数学",
            "高等代数",
            "高等数学习题",
            "高等代数习题",
            "大学英语",
            "军事理论（思政）",
            "思想道德与法治（思政）",
            "大学物理",
            "普通物理",
            "An English Topic",
            "An English topic with some 中文 in it",
            "Emojis in the topic name 😂😂😂",
            "Emojis in the topic name 🧑‍🦲",
            "Emojis in the topic name 😂😂😂 with some 中文 in it",
            "Emojis in the topic name 🧑‍🦲 with some 中文 in it",
        ]
        for name in topics:
            response = self.client.post(
                "/topics",
                headers=self.headers,
                json={"name": f"{self.topic_prefix} {name}"},
            )
            assert response.status_code == 201
            data = response.json()
            assert data["code"] == 201
            assert data["data"]["id"] is not None
            self.topic_ids.append(data["data"]["id"])
        assert len(self.topic_ids) == 15

    def test_create_topic_no_auth(self):
        response = self.client.post(
            "/topics",
            json={"name": f"{self.topic_prefix} 高等数学"},
        )
        assert response.status_code == 401

    def test_create_topic_invalid_token(self):
        response = self.client.post(
            "/topics",
            headers={"Authorization": "Bearer invalid_token_123"},
            json={"name": f"{self.topic_prefix} 高等数学"},
        )
        assert response.status_code == 401

    def test_create_topic_already_exists(self):
        topic_name = f"{self.topic_prefix} 唯一话题测试"
        self.client.post(
            "/topics",
            headers=self.headers,
            json={"name": topic_name},
        )
        response = self.client.post(
            "/topics",
            headers=self.headers,
            json={"name": topic_name},
        )
        assert response.status_code == 409


class TestTopicsSearchIntegration:
    """Tests for searching topics."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.topic_code = str(unique_int(1000000000, 9999999999))
        self.topic_prefix = f"[Test({self.topic_code}) Topic]"
        self.topic_ids: list[int] = []
        topics = [
            "高等数学",
            "高等代数",
            "高等数学习题",
            "高等代数习题",
            "大学英语",
            "军事理论（思政）",
            "思想道德与法治（思政）",
            "大学物理",
            "普通物理",
            "An English Topic",
            "An English topic with some 中文 in it",
            "Emojis in the topic name 😂😂😂",
            "Emojis in the topic name 🧑‍🦲",
            "Emojis in the topic name 😂😂😂 with some 中文 in it",
            "Emojis in the topic name 🧑‍🦲 with some 中文 in it",
        ]
        for name in topics:
            resp = self.client.post(
                "/topics",
                headers=self.headers,
                json={"name": f"{self.topic_prefix} {name}"},
            )
            self.topic_ids.append(resp.json()["data"]["id"])
        time.sleep(0.5)

    def test_search_empty_query_returns_empty_page(self):
        response = self.client.get(
            "/topics",
            headers=self.headers,
            params={"q": ""},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["topics"]) == 0
        assert data["data"]["page"]["pageSize"] == 0
        assert data["data"]["page"]["pageStart"] == 0
        assert data["data"]["page"]["hasPrev"] is False
        assert data["data"]["page"]["prevStart"] == 0
        assert data["data"]["page"]["hasMore"] is False
        assert data["data"]["page"]["nextStart"] == 0

    def test_search_topics_and_paging(self):
        time.sleep(0.5)
        response = self.client.get(
            "/topics",
            headers=self.headers,
            params={"q": f"{self.topic_code} 高等"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["topics"]) >= 4
        for i in range(min(4, len(data["data"]["topics"]))):
            assert self.topic_code in data["data"]["topics"][i]["name"]

        response2 = self.client.get(
            "/topics",
            headers=self.headers,
            params={"q": f"{self.topic_code} 高等", "page_size": 3},
        )
        assert response2.status_code == 200
        data2 = response2.json()
        assert data2["code"] == 200
        assert len(data2["data"]["topics"]) == 3
        assert self.topic_code in data2["data"]["topics"][0]["name"]
        assert "高等" in data2["data"]["topics"][0]["name"]
        assert self.topic_code in data2["data"]["topics"][1]["name"]
        assert "高等" in data2["data"]["topics"][1]["name"]
        assert self.topic_code in data2["data"]["topics"][2]["name"]
        assert "高等" in data2["data"]["topics"][2]["name"]
        assert data2["data"]["page"]["pageSize"] == 3
        assert data2["data"]["page"]["hasPrev"] is False
        assert data2["data"]["page"]["prevStart"] == 0
        assert data2["data"]["page"]["hasMore"] is True

        response3 = self.client.get(
            "/topics",
            headers=self.headers,
            params={
                "q": f"{self.topic_code} 高等",
                "page_size": 3,
                "page_start": data2["data"]["page"]["nextStart"],
            },
        )
        assert response3.status_code == 200
        data3 = response3.json()
        assert data3["code"] == 200
        assert len(data3["data"]["topics"]) >= 1
        assert data3["data"]["topics"][0]["id"] == data2["data"]["page"]["nextStart"]
        assert self.topic_code in data3["data"]["topics"][0]["name"]
        assert data3["data"]["page"]["pageStart"] == data3["data"]["topics"][0]["id"]
        assert data3["data"]["page"]["hasPrev"] is True
        assert data3["data"]["page"]["prevStart"] == data2["data"]["topics"][0]["id"]

        response4 = self.client.get(
            "/topics",
            headers=self.headers,
            params={
                "q": f"{self.topic_code} 高等",
                "page_size": 3,
                "page_start": data2["data"]["page"]["pageStart"],
            },
        )
        assert response4.status_code == 200
        data4 = response4.json()
        assert data4["data"]["topics"] == data2["data"]["topics"]

    def test_search_emoji_topics(self):
        time.sleep(0.5)
        response = self.client.get(
            "/topics",
            headers=self.headers,
            params={"q": f"{self.topic_code} 🧑‍🦲", "page_size": 3},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["topics"]) >= 2
        found_names = [t["name"] for t in data["data"]["topics"]]
        emoji_count = sum(1 for n in found_names if "🧑‍🦲" in n)
        assert emoji_count >= 2

    def test_search_returns_empty_for_nonexistent(self):
        response = self.client.get(
            "/topics",
            headers=self.headers,
            params={"q": "毳毳毳毳"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["topics"]) == 0
        assert data["data"]["page"]["pageStart"] == 0
        assert data["data"]["page"]["pageSize"] == 0
        assert data["data"]["page"]["hasPrev"] is False
        assert data["data"]["page"]["prevStart"] == 0
        assert data["data"]["page"]["hasMore"] is False
        assert data["data"]["page"]["nextStart"] == 0

    def test_search_invalid_page_start(self):
        response = self.client.get(
            "/topics",
            headers=self.headers,
            params={"q": "something", "page_start": -1},
        )
        assert response.status_code == 404

    def test_search_bad_page_start_format(self):
        response = self.client.get(
            "/topics",
            headers=self.headers,
            params={"q": "something", "page_start": "abc"},
        )
        assert response.status_code in (400, 422)

    def test_search_no_auth(self):
        response = self.client.get(
            "/topics",
            params={"q": "something"},
        )
        assert response.status_code == 401


class TestTopicsGetIntegration:
    """Tests for getting topics by id."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.topic_code = str(unique_int(1000000000, 9999999999))
        self.topic_prefix = f"[Test({self.topic_code}) Topic]"
        resp = self.client.post(
            "/topics",
            headers=self.headers,
            json={"name": f"{self.topic_prefix} 高等数学"},
        )
        self.topic_id = resp.json()["data"]["id"]

    def test_get_topic(self):
        response = self.client.get(
            f"/topics/{self.topic_id}",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert data["data"]["topic"]["id"] == self.topic_id
        assert data["data"]["topic"]["name"] == f"{self.topic_prefix} 高等数学"

    def test_get_topic_not_found(self):
        response = self.client.get(
            "/topics/-1",
            headers=self.headers,
        )
        assert response.status_code == 404

    def test_get_topic_bad_id_format(self):
        response = self.client.get(
            "/topics/abc",
            headers=self.headers,
        )
        assert response.status_code in (400, 422)

    def test_get_topic_no_auth(self):
        response = self.client.get(
            f"/topics/{self.topic_id}",
        )
        assert response.status_code == 401
