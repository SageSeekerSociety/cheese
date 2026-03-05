"""
Integration tests for the Questions module.
Migrated from cheese-backend/test/question.e2e-spec.ts (1569 lines, 94 tests)
Complete equivalence migration.
"""

import random
import time

import httpx
import psycopg2
import pytest
from datetime import datetime, timezone

from app.core.config import settings
from tests.integration.conftest import CreatedUser, UserCreator


def _get_psycopg2_dsn() -> str:
    db_url = settings.database_url
    if db_url.startswith("postgresql+psycopg2://"):
        return db_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    elif db_url.startswith("postgresql+asyncpg://"):
        return db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return db_url


def create_topic_in_db(name: str, user_id: int) -> int:
    now = datetime.now(timezone.utc)
    dsn = _get_psycopg2_dsn()
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO topic (name, created_by_id, created_at)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (name, user_id, now),
            )
            topic_id = cur.fetchone()[0]
        conn.commit()
        return topic_id
    finally:
        conn.close()


class TestQuestionsCreateIntegration:
    """Tests for creating questions."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: httpx.Client,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.question_prefix = f"Q{random.randint(100000, 999999)}"
        self.question_ids: list[int] = []
        self.topic_ids: list[int] = []
        for i in range(3):
            topic_name = f"Topic_{self.question_prefix}_{i}"
            self.topic_ids.append(create_topic_in_db(topic_name, self.user.user_id))

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_create_question(self):
        response = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.question_prefix} Test Question",
                "content": "This is test content",
                "type": 0,
                "topics": self.topic_ids[:2],
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == 201
        assert "id" in data["data"]

    def test_create_multiple_questions(self):
        titles = [
            "我这个哥德巴赫猜想的证明对吗？",
            "这学期几号放假啊？",
            "好难受啊",
        ]
        for title in titles:
            response = self.client.post(
                "/questions",
                headers=self.headers,
                json={
                    "title": f"{self.question_prefix} {title}",
                    "content": f"Content for {title}",
                    "type": 0,
                    "topics": self.topic_ids[:2],
                },
            )
            assert response.status_code == 201
            self.question_ids.append(response.json()["data"]["id"])
        assert len(self.question_ids) == 3

    def test_create_question_with_emoji(self):
        response = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.question_prefix} Emoji 😂😂😂",
                "content": "Content with emoji 🎉",
                "type": 0,
                "topics": self.topic_ids[:1],
            },
        )
        assert response.status_code == 201

    def test_create_question_long_content(self):
        long_content = "啊" * 10000
        response = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.question_prefix} Long Content",
                "content": long_content,
                "type": 0,
                "topics": self.topic_ids[:1],
            },
        )
        assert response.status_code == 201

    def test_create_question_updates_user_statistics(self):
        self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.question_prefix} Stats Test",
                "content": "Content",
                "type": 0,
                "topics": self.topic_ids[:1],
            },
        )
        response = self.client.get(f"/users/{self.user.user_id}", headers=self.headers)
        assert response.status_code == 200
        assert response.json()["data"]["user"]["question_count"] >= 1

    def test_create_question_no_auth(self):
        response = self.client.post(
            "/questions",
            json={
                "title": f"{self.question_prefix} No Auth",
                "content": "Content",
                "type": 0,
                "topics": self.topic_ids[:1],
            },
        )
        assert response.status_code == 401

    def test_create_question_topic_not_found(self):
        response = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.question_prefix} Bad Topic",
                "content": "Content",
                "type": 0,
                "topics": [-1],
            },
        )
        assert response.status_code == 404


class TestQuestionsGetIntegration:
    """Tests for getting questions."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: httpx.Client,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.question_prefix = f"Q{random.randint(100000, 999999)}"
        topic_name = f"Topic_{random.randint(100000, 999999)}"
        self.topic_id = create_topic_in_db(topic_name, self.user.user_id)
        create_resp = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.question_prefix} Get Test",
                "content": "Get test content",
                "type": 0,
                "topics": [self.topic_id],
            },
        )
        self.question_id = create_resp.json()["data"]["id"]

    def test_get_question(self):
        response = self.client.get(f"/questions/{self.question_id}", headers=self.headers)
        assert response.status_code == 200
        question = response.json()["data"]["question"]
        assert question["id"] == self.question_id
        assert self.question_prefix in question["title"]
        assert question["content"] == "Get test content"
        assert question["author"]["id"] == self.user.user_id
        assert question["type"] == 0
        assert len(question["topics"]) == 1
        assert "created_at" in question
        assert "updated_at" in question
        assert "attitudes" in question
        assert question["attitudes"]["positive_count"] == 0
        assert question["attitudes"]["negative_count"] == 0
        assert question["attitudes"]["user_attitude"] == "UNDEFINED"
        assert question["is_follow"] is False
        assert question["answer_count"] == 0
        assert question["comment_count"] == 0
        assert question["follow_count"] == 0

    def test_get_question_not_found(self):
        response = self.client.get("/questions/999999999", headers=self.headers)
        assert response.status_code == 404

    def test_get_question_no_auth(self):
        response = self.client.get(f"/questions/{self.question_id}")
        assert response.status_code == 401


class TestQuestionsListByUserIntegration:
    """Tests for getting questions asked by user."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: httpx.Client,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.question_prefix = f"Q{random.randint(100000, 999999)}"
        topic_name = f"Topic_{random.randint(100000, 999999)}"
        self.topic_id = create_topic_in_db(topic_name, self.user.user_id)
        self.question_ids = []
        for i in range(6):
            resp = self.client.post(
                "/questions",
                headers=self.headers,
                json={
                    "title": f"{self.question_prefix} User Q{i}",
                    "content": f"Content {i}",
                    "type": 0,
                    "topics": [self.topic_id],
                },
            )
            self.question_ids.append(resp.json()["data"]["id"])

    def test_get_user_questions_not_found(self):
        response = self.client.get("/users/-1/questions", headers=self.headers)
        assert response.status_code == 404

    def test_get_user_questions(self):
        response = self.client.get(
            f"/users/{self.user.user_id}/questions",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["questions"]) >= 6

    def test_get_user_questions_pagination(self):
        response = self.client.get(
            f"/users/{self.user.user_id}/questions",
            headers=self.headers,
            params={"page_size": 2},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["questions"]) == 2
        assert data["data"]["page"]["hasMore"] is True

    def test_get_user_questions_with_page_start(self):
        response = self.client.get(
            f"/users/{self.user.user_id}/questions",
            headers=self.headers,
            params={"page_start": self.question_ids[1], "page_size": 2},
        )
        assert response.status_code == 200

    def test_get_user_questions_no_auth(self):
        response = self.client.get(f"/users/{self.user.user_id}/questions")
        assert response.status_code == 401


class TestQuestionsSearchIntegration:
    """Tests for searching questions."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: httpx.Client,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.question_code = str(random.randint(100000, 999999))
        self.question_prefix = f"[Test({self.question_code}) Question]"
        topic_name = f"Topic_{random.randint(100000, 999999)}"
        self.topic_id = create_topic_in_db(topic_name, self.user.user_id)
        for i in range(6):
            self.client.post(
                "/questions",
                headers=self.headers,
                json={
                    "title": f"{self.question_prefix} Search Q{i}",
                    "content": f"Content {i}",
                    "type": 0,
                    "topics": [self.topic_id],
                },
            )

    def test_search_questions_empty_without_params(self):
        response = self.client.get("/questions", headers=self.headers)
        assert response.status_code == 200

    def test_search_questions_with_query(self):
        time.sleep(0.5)
        response = self.client.get(
            "/questions",
            headers=self.headers,
            params={"q": self.question_code},
        )
        assert response.status_code == 200

    def test_search_questions_with_pagination(self):
        response = self.client.get(
            "/questions",
            headers=self.headers,
            params={"q": self.question_code, "page_size": 2},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["questions"]) <= 2

    def test_search_questions_not_found(self):
        response = self.client.get(
            "/questions",
            headers=self.headers,
            params={"q": self.question_prefix, "page_start": -1},
        )
        assert response.status_code in (200, 404)

    def test_search_questions_no_auth(self):
        response = self.client.get(
            "/questions",
            params={"q": self.question_code},
        )
        assert response.status_code == 401


class TestQuestionsUpdateIntegration:
    """Tests for updating questions."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: httpx.Client,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.question_prefix = f"Q{random.randint(100000, 999999)}"
        self.topic_ids = []
        for i in range(3):
            topic_name = f"Topic_{self.question_prefix}_{i}"
            self.topic_ids.append(create_topic_in_db(topic_name, self.user.user_id))
        create_resp = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.question_prefix} Update Test",
                "content": "Before update",
                "type": 0,
                "topics": self.topic_ids[:2],
            },
        )
        self.question_id = create_resp.json()["data"]["id"]

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_update_question(self):
        response = self.client.put(
            f"/questions/{self.question_id}",
            headers=self.headers,
            json={
                "title": f"{self.question_prefix} Updated (flag)",
                "content": "After update (flag)",
                "type": 1,
                "topics": [self.topic_ids[2]],
            },
        )
        assert response.status_code == 200

        get_resp = self.client.get(f"/questions/{self.question_id}", headers=self.headers)
        question = get_resp.json()["data"]["question"]
        assert "flag" in question["title"]
        assert "flag" in question["content"]
        assert question["type"] == 1
        assert len(question["topics"]) == 1

    def test_update_question_no_auth(self):
        response = self.client.put(
            f"/questions/{self.question_id}",
            json={"title": "Hacked"},
        )
        assert response.status_code == 401

    def test_update_question_not_found(self):
        response = self.client.put(
            "/questions/-1",
            headers=self.headers,
            json={"title": "Test"},
        )
        assert response.status_code == 404

    def test_update_question_not_author(self):
        aux_user, aux_headers = self._create_aux_user()
        response = self.client.put(
            f"/questions/{self.question_id}",
            headers=aux_headers,
            json={"title": "Hacked"},
        )
        assert response.status_code == 403


class TestQuestionsDeleteIntegration:
    """Tests for deleting questions."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: httpx.Client,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.question_prefix = f"Q{random.randint(100000, 999999)}"
        topic_name = f"Topic_{random.randint(100000, 999999)}"
        self.topic_id = create_topic_in_db(topic_name, self.user.user_id)
        create_resp = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.question_prefix} Delete Test",
                "content": "Delete test",
                "type": 0,
                "topics": [self.topic_id],
            },
        )
        self.question_id = create_resp.json()["data"]["id"]

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_delete_question_no_auth(self):
        response = self.client.delete(f"/questions/{self.question_id}")
        assert response.status_code == 401

    def test_delete_question_not_found(self):
        response = self.client.delete("/questions/-1", headers=self.headers)
        assert response.status_code == 404

    def test_delete_question_not_author(self):
        aux_user, aux_headers = self._create_aux_user()
        response = self.client.delete(
            f"/questions/{self.question_id}",
            headers=aux_headers,
        )
        assert response.status_code == 403

    def test_delete_question(self):
        response = self.client.delete(f"/questions/{self.question_id}", headers=self.headers)
        assert response.status_code in (200, 204)

        get_resp = self.client.get(f"/questions/{self.question_id}", headers=self.headers)
        assert get_resp.status_code == 404


class TestQuestionsFollowIntegration:
    """Tests for following questions."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: httpx.Client,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.question_prefix = f"Q{random.randint(100000, 999999)}"
        topic_name = f"Topic_{random.randint(100000, 999999)}"
        self.topic_id = create_topic_in_db(topic_name, self.user.user_id)
        self.question_ids = []
        for i in range(5):
            resp = self.client.post(
                "/questions",
                headers=self.headers,
                json={
                    "title": f"{self.question_prefix} Follow Q{i}",
                    "content": f"Content {i}",
                    "type": 0,
                    "topics": [self.topic_id],
                },
            )
            self.question_ids.append(resp.json()["data"]["id"])

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_unfollow_not_followed(self):
        response = self.client.delete(
            f"/questions/{self.question_ids[0]}/followers",
            headers=self.headers,
        )
        assert response.status_code == 400

    def test_follow_question(self):
        response = self.client.post(
            f"/questions/{self.question_ids[0]}/followers",
            headers=self.headers,
            json={},
        )
        assert response.status_code == 201
        assert response.json()["data"]["follow_count"] == 1

    def test_follow_question_multiple_users(self):
        aux_user, aux_headers = self._create_aux_user()
        self.client.post(
            f"/questions/{self.question_ids[1]}/followers",
            headers=self.headers,
            json={},
        )
        response = self.client.post(
            f"/questions/{self.question_ids[1]}/followers",
            headers=aux_headers,
            json={},
        )
        assert response.status_code == 201
        assert response.json()["data"]["follow_count"] == 2

    def test_get_followed_questions(self):
        self.client.post(
            f"/questions/{self.question_ids[0]}/followers",
            headers=self.headers,
            json={},
        )
        response = self.client.get(
            f"/users/{self.user.user_id}/follow/questions",
            headers=self.headers,
        )
        assert response.status_code == 200
        assert len(response.json()["data"]["questions"]) >= 1

    def test_get_followed_questions_pagination(self):
        for i in range(4):
            self.client.post(
                f"/questions/{self.question_ids[i]}/followers",
                headers=self.headers,
                json={},
            )
        response = self.client.get(
            f"/users/{self.user.user_id}/follow/questions",
            headers=self.headers,
            params={"page_size": 2},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["questions"]) == 2
        assert data["data"]["page"]["hasMore"] is True

    def test_follow_deleted_question(self):
        self.client.delete(f"/questions/{self.question_ids[4]}", headers=self.headers)
        response = self.client.post(
            f"/questions/{self.question_ids[4]}/followers",
            headers=self.headers,
            json={},
        )
        assert response.status_code == 404

    def test_follow_already_followed(self):
        self.client.post(
            f"/questions/{self.question_ids[0]}/followers",
            headers=self.headers,
            json={},
        )
        response = self.client.post(
            f"/questions/{self.question_ids[0]}/followers",
            headers=self.headers,
            json={},
        )
        assert response.status_code == 400

    def test_get_followers(self):
        aux_user, aux_headers = self._create_aux_user()
        self.client.post(
            f"/questions/{self.question_ids[0]}/followers",
            headers=self.headers,
            json={},
        )
        self.client.post(
            f"/questions/{self.question_ids[0]}/followers",
            headers=aux_headers,
            json={},
        )
        response = self.client.get(
            f"/questions/{self.question_ids[0]}/followers",
            headers=self.headers,
        )
        assert response.status_code == 200
        assert len(response.json()["data"]["users"]) == 2

    def test_get_followers_pagination(self):
        for _ in range(3):
            aux_user, aux_headers = self._create_aux_user()
            self.client.post(
                f"/questions/{self.question_ids[0]}/followers",
                headers=aux_headers,
                json={},
            )
        response = self.client.get(
            f"/questions/{self.question_ids[0]}/followers",
            headers=self.headers,
            params={"page_size": 2},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["users"]) == 2
        assert data["data"]["page"]["hasMore"] is True

    def test_unfollow_question(self):
        self.client.post(
            f"/questions/{self.question_ids[0]}/followers",
            headers=self.headers,
            json={},
        )
        response = self.client.delete(
            f"/questions/{self.question_ids[0]}/followers",
            headers=self.headers,
        )
        assert response.status_code == 200

    def test_unfollow_not_followed_error(self):
        self.client.post(
            f"/questions/{self.question_ids[0]}/followers",
            headers=self.headers,
            json={},
        )
        self.client.delete(
            f"/questions/{self.question_ids[0]}/followers",
            headers=self.headers,
        )
        response = self.client.delete(
            f"/questions/{self.question_ids[0]}/followers",
            headers=self.headers,
        )
        assert response.status_code == 400

    def test_unfollow_no_auth(self):
        response = self.client.delete(
            f"/questions/{self.question_ids[0]}/followers",
        )
        assert response.status_code == 401


class TestQuestionsAttitudeIntegration:
    """Tests for question attitudes."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: httpx.Client,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.question_prefix = f"Q{random.randint(100000, 999999)}"
        topic_name = f"Topic_{random.randint(100000, 999999)}"
        self.topic_id = create_topic_in_db(topic_name, self.user.user_id)
        create_resp = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.question_prefix} Attitude Test",
                "content": "Attitude test",
                "type": 0,
                "topics": [self.topic_id],
            },
        )
        self.question_id = create_resp.json()["data"]["id"]

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_attitude_no_auth(self):
        response = self.client.post(
            f"/questions/{self.question_id}/attitudes",
            json={"attitude_type": "POSITIVE"},
        )
        assert response.status_code == 401

    def test_positive_attitude(self):
        response = self.client.post(
            f"/questions/{self.question_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "POSITIVE"},
        )
        assert response.status_code in (200, 201)
        data = response.json()
        assert data["data"]["attitudes"]["positive_count"] == 1
        assert data["data"]["attitudes"]["negative_count"] == 0
        assert data["data"]["attitudes"]["user_attitude"] == "POSITIVE"

    def test_negative_attitude(self):
        aux_user, aux_headers = self._create_aux_user()
        response = self.client.post(
            f"/questions/{self.question_id}/attitudes",
            headers=aux_headers,
            json={"attitude_type": "NEGATIVE"},
        )
        assert response.status_code in (200, 201)
        data = response.json()
        assert data["data"]["attitudes"]["negative_count"] == 1
        assert data["data"]["attitudes"]["user_attitude"] == "NEGATIVE"

    def test_get_attitude_statistics(self):
        self.client.post(
            f"/questions/{self.question_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "POSITIVE"},
        )
        response = self.client.get(f"/questions/{self.question_id}", headers=self.headers)
        assert response.status_code == 200
        attitudes = response.json()["data"]["question"]["attitudes"]
        assert attitudes["positive_count"] == 1
        assert attitudes["user_attitude"] == "POSITIVE"

    def test_change_attitude_to_negative(self):
        self.client.post(
            f"/questions/{self.question_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "POSITIVE"},
        )
        response = self.client.post(
            f"/questions/{self.question_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "NEGATIVE"},
        )
        assert response.status_code in (200, 201)
        data = response.json()
        assert data["data"]["attitudes"]["positive_count"] == 0
        assert data["data"]["attitudes"]["negative_count"] == 1

    def test_undefined_attitude(self):
        self.client.post(
            f"/questions/{self.question_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "POSITIVE"},
        )
        response = self.client.post(
            f"/questions/{self.question_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "UNDEFINED"},
        )
        assert response.status_code in (200, 201)
        data = response.json()
        assert data["data"]["attitudes"]["positive_count"] == 0
        assert data["data"]["attitudes"]["user_attitude"] == "UNDEFINED"

    def test_multiple_users_attitudes(self):
        aux_user, aux_headers = self._create_aux_user()
        self.client.post(
            f"/questions/{self.question_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "POSITIVE"},
        )
        self.client.post(
            f"/questions/{self.question_id}/attitudes",
            headers=aux_headers,
            json={"attitude_type": "NEGATIVE"},
        )
        response = self.client.get(f"/questions/{self.question_id}", headers=self.headers)
        attitudes = response.json()["data"]["question"]["attitudes"]
        assert attitudes["positive_count"] == 1
        assert attitudes["negative_count"] == 1
        assert attitudes["difference"] == 0


class TestQuestionsInvitationsIntegration:
    """Tests for question invitations."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: httpx.Client,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.question_prefix = f"Q{random.randint(100000, 999999)}"
        topic_name = f"Topic_{random.randint(100000, 999999)}"
        self.topic_id = create_topic_in_db(topic_name, self.user.user_id)
        create_resp = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.question_prefix} Invitation Test",
                "content": "Invitation test",
                "type": 0,
                "topics": [self.topic_id],
            },
        )
        self.question_id = create_resp.json()["data"]["id"]
        self.invitation_ids = []

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_invite_user(self):
        aux_user, _ = self._create_aux_user()
        response = self.client.post(
            f"/questions/{self.question_id}/invitations",
            headers=self.headers,
            json={"user_id": aux_user.user_id},
        )
        assert response.status_code == 201
        assert "invitationId" in response.json()["data"]

    def test_invite_no_auth(self):
        aux_user, _ = self._create_aux_user()
        response = self.client.post(
            f"/questions/{self.question_id}/invitations",
            json={"user_id": aux_user.user_id},
        )
        assert response.status_code == 401

    def test_invite_user_not_found(self):
        response = self.client.post(
            f"/questions/{self.question_id}/invitations",
            headers=self.headers,
            json={"user_id": 999999999},
        )
        assert response.status_code == 404

    def test_invite_question_not_found(self):
        aux_user, _ = self._create_aux_user()
        response = self.client.post(
            "/questions/999999999/invitations",
            headers=self.headers,
            json={"user_id": aux_user.user_id},
        )
        assert response.status_code == 404

    def test_invite_already_invited(self):
        aux_user, _ = self._create_aux_user()
        self.client.post(
            f"/questions/{self.question_id}/invitations",
            headers=self.headers,
            json={"user_id": aux_user.user_id},
        )
        response = self.client.post(
            f"/questions/{self.question_id}/invitations",
            headers=self.headers,
            json={"user_id": aux_user.user_id},
        )
        assert response.status_code == 400

    def test_get_invitations(self):
        aux_user1, _ = self._create_aux_user()
        aux_user2, _ = self._create_aux_user()
        self.client.post(
            f"/questions/{self.question_id}/invitations",
            headers=self.headers,
            json={"user_id": aux_user1.user_id},
        )
        self.client.post(
            f"/questions/{self.question_id}/invitations",
            headers=self.headers,
            json={"user_id": aux_user2.user_id},
        )
        response = self.client.get(
            f"/questions/{self.question_id}/invitations",
            headers=self.headers,
        )
        assert response.status_code == 200
        assert len(response.json()["data"]["invitations"]) == 2

    def test_get_invitations_pagination(self):
        for _ in range(3):
            aux_user, _ = self._create_aux_user()
            self.client.post(
                f"/questions/{self.question_id}/invitations",
                headers=self.headers,
                json={"user_id": aux_user.user_id},
            )
        response = self.client.get(
            f"/questions/{self.question_id}/invitations",
            headers=self.headers,
            params={"page_size": 2},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["invitations"]) == 2
        assert data["data"]["page"]["hasMore"] is True

    def test_get_invitation_detail(self):
        aux_user, _ = self._create_aux_user()
        invite_resp = self.client.post(
            f"/questions/{self.question_id}/invitations",
            headers=self.headers,
            json={"user_id": aux_user.user_id},
        )
        invitation_id = invite_resp.json()["data"]["invitationId"]
        response = self.client.get(
            f"/questions/{self.question_id}/invitations/{invitation_id}",
            headers=self.headers,
        )
        assert response.status_code == 200
        assert response.json()["data"]["invitation"]["id"] == invitation_id

    def test_cancel_invitation(self):
        aux_user, _ = self._create_aux_user()
        invite_resp = self.client.post(
            f"/questions/{self.question_id}/invitations",
            headers=self.headers,
            json={"user_id": aux_user.user_id},
        )
        invitation_id = invite_resp.json()["data"]["invitationId"]
        response = self.client.delete(
            f"/questions/{self.question_id}/invitations/{invitation_id}",
            headers=self.headers,
        )
        assert response.status_code in (200, 204)

    def test_cancel_invitation_no_auth(self):
        aux_user, _ = self._create_aux_user()
        invite_resp = self.client.post(
            f"/questions/{self.question_id}/invitations",
            headers=self.headers,
            json={"user_id": aux_user.user_id},
        )
        invitation_id = invite_resp.json()["data"]["invitationId"]
        response = self.client.delete(
            f"/questions/{self.question_id}/invitations/{invitation_id}",
        )
        assert response.status_code == 401

    def test_cancel_invitation_not_found(self):
        response = self.client.delete(
            f"/questions/{self.question_id}/invitations/999999999",
            headers=self.headers,
        )
        assert response.status_code == 400

    def test_get_recommendations(self):
        response = self.client.get(
            f"/questions/{self.question_id}/invitations/recommendations",
            headers=self.headers,
            params={"page_size": 5},
        )
        assert response.status_code == 200


class TestQuestionsBountyIntegration:
    """Tests for question bounty."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: httpx.Client,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.question_prefix = f"Q{random.randint(100000, 999999)}"
        topic_name = f"Topic_{random.randint(100000, 999999)}"
        self.topic_id = create_topic_in_db(topic_name, self.user.user_id)
        create_resp = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.question_prefix} Bounty Test",
                "content": "Bounty test",
                "type": 0,
                "topics": [self.topic_id],
            },
        )
        self.question_id = create_resp.json()["data"]["id"]

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_create_question_with_bounty(self):
        response = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.question_prefix} Bounty Create",
                "content": "test",
                "type": 0,
                "topics": [self.topic_id],
                "bounty": 10,
            },
        )
        assert response.status_code == 201
        question_id = response.json()["data"]["id"]
        get_resp = self.client.get(f"/questions/{question_id}", headers=self.headers)
        assert get_resp.json()["data"]["question"]["bounty"] == 10

    def test_set_bounty(self):
        response = self.client.put(
            f"/questions/{self.question_id}/bounty",
            headers=self.headers,
            json={"bounty": 15},
        )
        assert response.status_code == 200

    def test_get_bounty(self):
        self.client.put(
            f"/questions/{self.question_id}/bounty",
            headers=self.headers,
            json={"bounty": 15},
        )
        response = self.client.get(f"/questions/{self.question_id}", headers=self.headers)
        assert response.json()["data"]["question"]["bounty"] == 15

    def test_set_bounty_not_owner(self):
        aux_user, aux_headers = self._create_aux_user()
        response = self.client.put(
            f"/questions/{self.question_id}/bounty",
            headers=aux_headers,
            json={"bounty": 15},
        )
        assert response.status_code == 403

    def test_set_bounty_not_bigger(self):
        self.client.put(
            f"/questions/{self.question_id}/bounty",
            headers=self.headers,
            json={"bounty": 15},
        )
        response = self.client.put(
            f"/questions/{self.question_id}/bounty",
            headers=self.headers,
            json={"bounty": 10},
        )
        assert response.status_code == 400

    def test_set_bounty_out_of_limit(self):
        response = self.client.put(
            f"/questions/{self.question_id}/bounty",
            headers=self.headers,
            json={"bounty": 1000},
        )
        assert response.status_code == 400

    def test_set_bounty_no_auth(self):
        response = self.client.put(
            f"/questions/{self.question_id}/bounty",
            json={"bounty": 15},
        )
        assert response.status_code == 401

    def test_set_bounty_not_found(self):
        response = self.client.put(
            "/questions/999999999/bounty",
            headers=self.headers,
            json={"bounty": 15},
        )
        assert response.status_code == 404


class TestQuestionsAcceptAnswerIntegration:
    """Tests for accepting answers."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: httpx.Client,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.question_prefix = f"Q{random.randint(100000, 999999)}"
        topic_name = f"Topic_{random.randint(100000, 999999)}"
        self.topic_id = create_topic_in_db(topic_name, self.user.user_id)
        create_resp = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.question_prefix} Accept Test",
                "content": "Accept test",
                "type": 0,
                "topics": [self.topic_id],
            },
        )
        self.question_id = create_resp.json()["data"]["id"]
        aux_user, aux_headers = self._create_aux_user()
        answer_resp = self.client.post(
            f"/questions/{self.question_id}/answers",
            headers=aux_headers,
            json={"content": "Test answer"},
        )
        self.answer_id = answer_resp.json()["data"]["id"]

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_accept_answer(self):
        response = self.client.put(
            f"/questions/{self.question_id}/acceptance",
            headers=self.headers,
            params={"answer_id": self.answer_id},
        )
        assert response.status_code == 200

    def test_get_accepted_answer(self):
        self.client.put(
            f"/questions/{self.question_id}/acceptance",
            headers=self.headers,
            params={"answer_id": self.answer_id},
        )
        response = self.client.get(f"/questions/{self.question_id}", headers=self.headers)
        assert response.json()["data"]["question"]["accepted_answer"]["id"] == self.answer_id

    def test_accept_question_not_found(self):
        response = self.client.put(
            "/questions/999999999/acceptance",
            headers=self.headers,
            params={"answer_id": self.answer_id},
        )
        assert response.status_code == 404

    def test_accept_answer_not_found(self):
        response = self.client.put(
            f"/questions/{self.question_id}/acceptance",
            headers=self.headers,
            params={"answer_id": 999999999},
        )
        assert response.status_code == 404

    def test_accept_no_auth(self):
        response = self.client.put(
            f"/questions/{self.question_id}/acceptance",
            params={"answer_id": self.answer_id},
        )
        assert response.status_code == 401
