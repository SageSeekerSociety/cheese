"""
Integration tests for the Answers module.
Migrated from cheese-backend/test/answer.e2e-spec.ts (988 lines, 51 tests)
Complete equivalence migration.
"""

from datetime import UTC, datetime

import pytest
from anyio.from_thread import BlockingPortal
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.topics.models import Topic
from tests.integration.conftest import CreatedUser, UserCreator, unique_int


def create_topic_in_db(
    db_session: AsyncSession, portal: BlockingPortal, name: str, user_id: int
) -> int:
    topic = Topic(name=name, created_by_id=user_id, created_at=datetime.now(UTC))

    async def _do() -> int:
        db_session.add(topic)
        await db_session.flush()
        return topic.id

    return portal.call(_do)


class TestAnswersCreateIntegration:
    """Tests for creating answers."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.db = db_session
        self.portal = _portal
        self.test_prefix = f"A{unique_int(100000, 999999)}"
        topic_name = f"Topic_{unique_int(100000, 999999)}"
        self.topic_id = create_topic_in_db(self.db, self.portal, topic_name, self.user.user_id)
        self.question_ids: list[int] = []
        self.answer_ids: list[int] = []
        for i in range(6):
            resp = self.client.post(
                "/questions",
                headers=self.headers,
                json={
                    "title": f"{self.test_prefix} Question {i}",
                    "content": f"Question content {i}",
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

    def test_create_answer(self):
        aux_user, aux_headers = self._create_aux_user()
        response = self.client.post(
            f"/questions/{self.question_ids[0]}/answers",
            headers=aux_headers,
            json={"content": "This is a test answer content"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == 201
        assert "id" in data["data"]
        self.answer_ids.append(data["data"]["id"])

    def test_create_multiple_answers(self):
        aux_user, aux_headers = self._create_aux_user()
        contents = [
            "你说得对，但是原神是一款由米哈游自主研发的开放世界游戏，后面忘了",
            "难道你真的是天才？",
            "1+1明明等于3",
            "Answer content with emoji: 😂😂",
            "烫烫烫" * 1000,
        ]
        for i, content in enumerate(contents):
            response = self.client.post(
                f"/questions/{self.question_ids[i]}/answers",
                headers=aux_headers,
                json={"content": content},
            )
            assert response.status_code == 201
            self.answer_ids.append(response.json()["data"]["id"])
        assert len(self.answer_ids) == 5

    def test_create_answer_already_answered(self):
        aux_user, aux_headers = self._create_aux_user()
        self.client.post(
            f"/questions/{self.question_ids[0]}/answers",
            headers=aux_headers,
            json={"content": "First answer"},
        )
        response = self.client.post(
            f"/questions/{self.question_ids[0]}/answers",
            headers=aux_headers,
            json={"content": "Second answer"},
        )
        assert response.status_code == 400

    def test_create_answer_updates_user_statistics_no_login(self):
        aux_user, aux_headers = self._create_aux_user()
        for i in range(5):
            self.client.post(
                f"/questions/{self.question_ids[i]}/answers",
                headers=aux_headers,
                json={"content": f"Answer {i}"},
            )
        response = self.client.get(f"/users/{aux_user.user_id}", headers=self.headers)
        assert response.status_code == 200
        assert response.json()["data"]["user"]["answer_count"] == 5

    def test_create_answer_updates_user_statistics(self):
        aux_user, aux_headers = self._create_aux_user()
        for i in range(5):
            self.client.post(
                f"/questions/{self.question_ids[i]}/answers",
                headers=aux_headers,
                json={"content": f"Answer {i}"},
            )
        response = self.client.get(f"/users/{aux_user.user_id}", headers=self.headers)
        assert response.status_code == 200
        assert response.json()["data"]["user"]["answer_count"] == 5

    def test_create_answer_no_auth(self):
        response = self.client.post(
            f"/questions/{self.question_ids[0]}/answers",
            json={"content": "No auth answer"},
        )
        assert response.status_code == 401


class TestAnswersGetIntegration:
    """Tests for getting answers."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.db = db_session
        self.portal = _portal
        self.test_prefix = f"A{unique_int(100000, 999999)}"
        topic_name = f"Topic_{unique_int(100000, 999999)}"
        self.topic_id = create_topic_in_db(self.db, self.portal, topic_name, self.user.user_id)
        create_q_resp = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.test_prefix} Get Answer Test",
                "content": "Get answer test content",
                "type": 0,
                "topics": [self.topic_id],
            },
        )
        self.question_id = create_q_resp.json()["data"]["id"]
        self.aux_user, self.aux_headers = self._create_aux_user()
        answer_resp = self.client.post(
            f"/questions/{self.question_id}/answers",
            headers=self.aux_headers,
            json={"content": "你说得对，但是原神是一款由米哈游自主研发的开放世界游戏，后面忘了"},
        )
        self.answer_id = answer_resp.json()["data"]["id"]

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_get_answer(self):
        response = self.client.get(
            f"/questions/{self.question_id}/answers/{self.answer_id}",
            headers=self.aux_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["question"]["id"] == self.question_id
        assert data["data"]["question"]["title"] is not None
        assert data["data"]["question"]["content"] is not None
        assert data["data"]["question"]["author"] is not None
        assert data["data"]["answer"]["id"] == self.answer_id
        assert data["data"]["answer"]["question_id"] == self.question_id
        assert "原神" in data["data"]["answer"]["content"]
        assert data["data"]["answer"]["author"]["id"] == self.aux_user.user_id
        assert "created_at" in data["data"]["answer"]
        assert "updated_at" in data["data"]["answer"]
        assert "attitudes" in data["data"]["answer"]
        assert data["data"]["answer"]["attitudes"]["positive_count"] == 0
        assert data["data"]["answer"]["attitudes"]["negative_count"] == 0
        assert data["data"]["answer"]["attitudes"]["difference"] == 0
        assert data["data"]["answer"]["attitudes"]["user_attitude"] == "UNDEFINED"
        assert data["data"]["answer"]["is_favorite"] is False
        assert data["data"]["answer"]["comment_count"] == 0
        assert data["data"]["answer"]["favorite_count"] == 0
        assert "view_count" in data["data"]["answer"]
        assert data["data"]["answer"]["is_group"] is False

    def test_get_answer_not_found(self):
        response = self.client.get(
            f"/questions/{self.question_id + 1}/answers/{self.answer_id}",
            headers=self.aux_headers,
        )
        assert response.status_code == 404

    def test_get_answer_no_auth(self):
        response = self.client.get(
            f"/questions/{self.question_id}/answers/{self.answer_id}",
        )
        assert response.status_code == 401


class TestAnswersByQuestionIntegration:
    """Tests for getting answers by question ID."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.db = db_session
        self.portal = _portal
        self.test_prefix = f"A{unique_int(100000, 999999)}"
        topic_name = f"Topic_{unique_int(100000, 999999)}"
        self.topic_id = create_topic_in_db(self.db, self.portal, topic_name, self.user.user_id)
        create_q_resp = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.test_prefix} Answers List Test",
                "content": "Answers list test content",
                "type": 0,
                "topics": [self.topic_id],
            },
        )
        self.question_id = create_q_resp.json()["data"]["id"]
        self.answer_ids: list[int] = []
        self.aux_users: list[tuple[CreatedUser, dict[str, str]]] = []
        for i in range(6):
            aux_user, aux_headers = self._create_aux_user()
            self.aux_users.append((aux_user, aux_headers))
            resp = self.client.post(
                f"/questions/{self.question_id}/answers",
                headers=aux_headers,
                json={"content": f"answer{i + 1}"},
            )
            self.answer_ids.append(resp.json()["data"]["id"])

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_get_answers_by_question_all(self):
        _, aux_headers = self.aux_users[0]
        response = self.client.get(
            f"/questions/{self.question_id}/answers",
            headers=aux_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["page"]["pageStart"] == self.answer_ids[0]
        assert data["data"]["page"]["pageSize"] == len(self.answer_ids)
        assert data["data"]["page"]["hasPrev"] is False
        assert data["data"]["page"]["prevStart"] == 0
        assert data["data"]["page"]["hasMore"] is False
        assert data["data"]["page"]["nextStart"] == 0
        assert len(data["data"]["answers"]) == len(self.answer_ids)
        for answer in data["data"]["answers"]:
            assert answer["question_id"] == self.question_id
        answer_ids_sorted = sorted([a["id"] for a in data["data"]["answers"]])
        assert answer_ids_sorted == sorted(self.answer_ids)

    def test_get_answers_by_question_all_again(self):
        _, aux_headers = self.aux_users[0]
        response = self.client.get(
            f"/questions/{self.question_id}/answers",
            headers=aux_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["page"]["pageStart"] == self.answer_ids[0]
        assert data["data"]["page"]["pageSize"] == len(self.answer_ids)
        assert data["data"]["page"]["hasPrev"] is False
        assert data["data"]["page"]["hasMore"] is False
        answer_ids_sorted = sorted([a["id"] for a in data["data"]["answers"]])
        assert answer_ids_sorted == sorted(self.answer_ids)

    def test_get_answers_by_question_with_page_start(self):
        _, aux_headers = self.aux_users[0]
        response = self.client.get(
            f"/questions/{self.question_id}/answers",
            headers=aux_headers,
            params={"page_start": self.answer_ids[0], "page_size": 20},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["page"]["pageSize"] == len(self.answer_ids)
        assert data["data"]["page"]["hasPrev"] is False
        assert data["data"]["page"]["hasMore"] is False
        answer_ids_sorted = sorted([a["id"] for a in data["data"]["answers"]])
        assert answer_ids_sorted == sorted(self.answer_ids)

    def test_get_answers_by_question_pagination(self):
        _, aux_headers = self.aux_users[0]
        response = self.client.get(
            f"/questions/{self.question_id}/answers",
            headers=aux_headers,
            params={"page_start": self.answer_ids[2], "page_size": 2},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["page"]["pageStart"] == self.answer_ids[2]
        assert data["data"]["page"]["pageSize"] == 2
        assert data["data"]["page"]["hasPrev"] is True
        assert data["data"]["page"]["prevStart"] == self.answer_ids[0]
        assert data["data"]["page"]["hasMore"] is True
        assert data["data"]["page"]["nextStart"] == self.answer_ids[4]
        assert len(data["data"]["answers"]) == 2
        assert data["data"]["answers"][0]["question_id"] == self.question_id
        assert data["data"]["answers"][1]["question_id"] == self.question_id
        assert data["data"]["answers"][0]["id"] == self.answer_ids[2]
        assert data["data"]["answers"][1]["id"] == self.answer_ids[3]

    def test_get_answers_question_not_found(self):
        response = self.client.get(
            "/questions/99999/answers",
            headers=self.headers,
        )
        assert response.status_code == 404

    def test_get_answers_by_question_no_auth(self):
        response = self.client.get(
            f"/questions/{self.question_id}/answers",
        )
        assert response.status_code == 401


class TestAnswersByUserIntegration:
    """Tests for getting answers by user ID."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.db = db_session
        self.portal = _portal
        self.test_prefix = f"A{unique_int(100000, 999999)}"
        topic_name = f"Topic_{unique_int(100000, 999999)}"
        self.topic_id = create_topic_in_db(self.db, self.portal, topic_name, self.user.user_id)
        self.aux_user, self.aux_headers = self._create_aux_user()
        self.answer_ids: list[int] = []
        for i in range(5):
            q_resp = self.client.post(
                "/questions",
                headers=self.headers,
                json={
                    "title": f"{self.test_prefix} Question {i}",
                    "content": f"Content {i}",
                    "type": 0,
                    "topics": [self.topic_id],
                },
            )
            question_id = q_resp.json()["data"]["id"]
            a_resp = self.client.post(
                f"/questions/{question_id}/answers",
                headers=self.aux_headers,
                json={"content": f"Answer {i} by aux user"},
            )
            self.answer_ids.append(a_resp.json()["data"]["id"])

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_get_user_answers_not_found(self):
        response = self.client.get("/users/-1/answers", headers=self.headers)
        assert response.status_code == 404

    def test_get_user_answers_default(self):
        response = self.client.get(
            f"/users/{self.aux_user.user_id}/answers",
            headers=self.aux_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["page"]["pageStart"] == self.answer_ids[0]
        assert data["data"]["page"]["pageSize"] == len(self.answer_ids)
        assert data["data"]["page"]["hasMore"] is False
        assert data["data"]["page"]["nextStart"] == 0
        assert len(data["data"]["answers"]) == len(self.answer_ids)
        for i, answer in enumerate(data["data"]["answers"]):
            assert answer["id"] == self.answer_ids[i]

    def test_get_user_answers_pagination(self):
        response = self.client.get(
            f"/users/{self.aux_user.user_id}/answers",
            headers=self.headers,
            params={"pageStart": self.answer_ids[0], "pageSize": 2},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["page"]["pageStart"] == self.answer_ids[0]
        assert data["data"]["page"]["pageSize"] == 2
        assert data["data"]["page"]["hasMore"] is True
        assert data["data"]["page"]["nextStart"] == self.answer_ids[2]
        assert len(data["data"]["answers"]) == 2
        assert data["data"]["answers"][0]["id"] == self.answer_ids[0]
        assert data["data"]["answers"][1]["id"] == self.answer_ids[1]

    def test_get_user_answers_pagination_middle(self):
        response = self.client.get(
            f"/users/{self.aux_user.user_id}/answers",
            headers=self.headers,
            params={"pageStart": self.answer_ids[2], "pageSize": 2},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["page"]["pageStart"] == self.answer_ids[2]
        assert data["data"]["page"]["pageSize"] == 2
        assert data["data"]["page"]["hasMore"] is True
        assert data["data"]["page"]["nextStart"] == self.answer_ids[4]
        assert len(data["data"]["answers"]) == 2
        assert data["data"]["answers"][0]["id"] == self.answer_ids[2]
        assert data["data"]["answers"][1]["id"] == self.answer_ids[3]

    def test_get_user_answers_no_auth(self):
        response = self.client.get(f"/users/{self.aux_user.user_id}/answers")
        assert response.status_code == 401


class TestAnswersUpdateIntegration:
    """Tests for updating answers."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.db = db_session
        self.portal = _portal
        self.test_prefix = f"A{unique_int(100000, 999999)}"
        topic_name = f"Topic_{unique_int(100000, 999999)}"
        self.topic_id = create_topic_in_db(self.db, self.portal, topic_name, self.user.user_id)
        q_resp = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.test_prefix} Update Test",
                "content": "Update test content",
                "type": 0,
                "topics": [self.topic_id],
            },
        )
        self.question_id = q_resp.json()["data"]["id"]
        self.aux_user, self.aux_headers = self._create_aux_user()
        a_resp = self.client.post(
            f"/questions/{self.question_id}/answers",
            headers=self.aux_headers,
            json={"content": "Original answer content"},
        )
        self.answer_id = a_resp.json()["data"]["id"]

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_update_answer_not_owner(self):
        response = self.client.put(
            f"/questions/{self.question_id}/answers/{self.answer_id}",
            headers=self.headers,
            json={"content": "Some content"},
        )
        assert response.status_code == 403

    def test_update_answer_success(self):
        updated_content = "--------更新----------"
        response = self.client.put(
            f"/questions/{self.question_id}/answers/{self.answer_id}",
            headers=self.aux_headers,
            json={"content": updated_content},
        )
        assert response.status_code == 200

    def test_update_answer_not_found(self):
        response = self.client.put(
            f"/questions/{self.question_id}/answers/999999",
            headers=self.aux_headers,
            json={"content": "Some content"},
        )
        assert response.status_code == 404

    def test_update_answer_no_auth(self):
        response = self.client.put(
            f"/questions/{self.question_id}/answers/{self.answer_id}",
            json={"content": "Updated content"},
        )
        assert response.status_code == 401

    def test_update_answer_wrong_question(self):
        response = self.client.put(
            f"/questions/{self.question_id + 1}/answers/{self.answer_id}",
            headers=self.aux_headers,
            json={"content": "Some content"},
        )
        assert response.status_code == 404


class TestAnswersDeleteIntegration:
    """Tests for deleting answers."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.db = db_session
        self.portal = _portal
        self.test_prefix = f"A{unique_int(100000, 999999)}"
        topic_name = f"Topic_{unique_int(100000, 999999)}"
        self.topic_id = create_topic_in_db(self.db, self.portal, topic_name, self.user.user_id)
        self.question_ids: list[int] = []
        self.answer_ids: list[int] = []
        for i in range(3):
            q_resp = self.client.post(
                "/questions",
                headers=self.headers,
                json={
                    "title": f"{self.test_prefix} Delete Test {i}",
                    "content": f"Delete test content {i}",
                    "type": 0,
                    "topics": [self.topic_id],
                },
            )
            self.question_ids.append(q_resp.json()["data"]["id"])
        self.aux_user, self.aux_headers = self._create_aux_user()
        for i, q_id in enumerate(self.question_ids):
            a_resp = self.client.post(
                f"/questions/{q_id}/answers",
                headers=self.aux_headers,
                json={"content": f"Answer to delete {i}"},
            )
            self.answer_ids.append(a_resp.json()["data"]["id"])

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_delete_answer_not_owner(self):
        response = self.client.delete(
            f"/questions/{self.question_ids[0]}/answers/{self.answer_ids[0]}",
            headers=self.headers,
        )
        assert response.status_code == 403

    def test_delete_answer_success(self):
        response = self.client.delete(
            f"/questions/{self.question_ids[2]}/answers/{self.answer_ids[2]}",
            headers=self.aux_headers,
        )
        assert response.status_code == 200

    def test_delete_answer_not_found(self):
        response = self.client.delete(
            f"/questions/{self.question_ids[0]}/answers/0",
            headers=self.aux_headers,
        )
        assert response.status_code == 404

    def test_delete_answer_no_auth(self):
        response = self.client.delete(
            f"/questions/{self.question_ids[0]}/answers/{self.answer_ids[0]}",
        )
        assert response.status_code == 401


class TestAnswersFavoriteIntegration:
    """Tests for favoriting answers."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.db = db_session
        self.portal = _portal
        self.test_prefix = f"A{unique_int(100000, 999999)}"
        topic_name = f"Topic_{unique_int(100000, 999999)}"
        self.topic_id = create_topic_in_db(self.db, self.portal, topic_name, self.user.user_id)
        self.question_ids: list[int] = []
        self.answer_ids: list[int] = []
        for i in range(5):
            q_resp = self.client.post(
                "/questions",
                headers=self.headers,
                json={
                    "title": f"{self.test_prefix} Favorite Test {i}",
                    "content": f"Favorite test content {i}",
                    "type": 0,
                    "topics": [self.topic_id],
                },
            )
            self.question_ids.append(q_resp.json()["data"]["id"])
        self.aux_user, self.aux_headers = self._create_aux_user()
        for i, q_id in enumerate(self.question_ids):
            a_resp = self.client.post(
                f"/questions/{q_id}/answers",
                headers=self.aux_headers,
                json={"content": f"Answer for favorite test {i}"},
            )
            self.answer_ids.append(a_resp.json()["data"]["id"])

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_favorite_answer(self):
        response = self.client.put(
            f"/questions/{self.question_ids[1]}/answers/{self.answer_ids[1]}/favorite",
            headers=self.aux_headers,
        )
        assert response.status_code == 200

    def test_unfavorite_answer(self):
        self.client.put(
            f"/questions/{self.question_ids[1]}/answers/{self.answer_ids[1]}/favorite",
            headers=self.aux_headers,
        )
        response = self.client.delete(
            f"/questions/{self.question_ids[1]}/answers/{self.answer_ids[1]}/favorite",
            headers=self.aux_headers,
        )
        assert response.status_code == 200

    def test_unfavorite_not_favorited(self):
        response = self.client.delete(
            f"/questions/{self.question_ids[4]}/answers/{self.answer_ids[4]}/favorite",
            headers=self.aux_headers,
        )
        assert response.status_code == 400

    def test_favorite_answer_not_found(self):
        response = self.client.put(
            f"/questions/{self.question_ids[0]}/answers/99999/favorite",
            headers=self.headers,
        )
        assert response.status_code == 404

    def test_unfavorite_answer_not_found(self):
        response = self.client.delete(
            f"/questions/{self.question_ids[0]}/answers/99998/favorite",
            headers=self.aux_headers,
        )
        assert response.status_code == 404

    def test_favorite_answer_no_auth(self):
        response = self.client.put(
            f"/questions/{self.question_ids[0]}/answers/{self.answer_ids[0]}/favorite",
        )
        assert response.status_code == 401


class TestAnswersAttitudeIntegration:
    """Tests for answer attitudes."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.db = db_session
        self.portal = _portal
        self.test_prefix = f"A{unique_int(100000, 999999)}"
        topic_name = f"Topic_{unique_int(100000, 999999)}"
        self.topic_id = create_topic_in_db(self.db, self.portal, topic_name, self.user.user_id)
        q_resp = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.test_prefix} Attitude Test",
                "content": "Attitude test content",
                "type": 0,
                "topics": [self.topic_id],
            },
        )
        self.question_id = q_resp.json()["data"]["id"]
        self.aux_user, self.aux_headers = self._create_aux_user()
        a_resp = self.client.post(
            f"/questions/{self.question_id}/answers",
            headers=self.aux_headers,
            json={"content": "Answer for attitude test"},
        )
        self.answer_id = a_resp.json()["data"]["id"]

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_attitude_no_auth(self):
        response = self.client.post(
            f"/questions/{self.question_id}/answers/{self.answer_id}/attitudes",
            json={"attitude_type": "POSITIVE"},
        )
        assert response.status_code == 401

    def test_set_positive_attitude(self):
        response = self.client.post(
            f"/questions/{self.question_id}/answers/{self.answer_id}/attitudes",
            headers=self.aux_headers,
            json={"attitude_type": "POSITIVE"},
        )
        assert response.status_code in (200, 201)
        data = response.json()
        assert data["data"]["attitudes"]["positive_count"] == 1
        assert data["data"]["attitudes"]["negative_count"] == 0
        assert data["data"]["attitudes"]["difference"] == 1
        assert data["data"]["attitudes"]["user_attitude"] == "POSITIVE"

    def test_set_negative_attitude(self):
        response = self.client.post(
            f"/questions/{self.question_id}/answers/{self.answer_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "NEGATIVE"},
        )
        assert response.status_code in (200, 201)
        data = response.json()
        assert data["data"]["attitudes"]["negative_count"] == 1
        assert data["data"]["attitudes"]["user_attitude"] == "NEGATIVE"

    def test_get_answer_no_auth_for_attitude(self):
        response = self.client.get(
            f"/questions/{self.question_id}/answers/{self.answer_id}",
        )
        assert response.status_code == 401

    def test_get_answer_with_attitude_stats_positive_user(self):
        self.client.post(
            f"/questions/{self.question_id}/answers/{self.answer_id}/attitudes",
            headers=self.aux_headers,
            json={"attitude_type": "POSITIVE"},
        )
        self.client.post(
            f"/questions/{self.question_id}/answers/{self.answer_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "NEGATIVE"},
        )
        response = self.client.get(
            f"/questions/{self.question_id}/answers/{self.answer_id}",
            headers=self.aux_headers,
        )
        assert response.status_code == 200
        attitudes = response.json()["data"]["answer"]["attitudes"]
        assert attitudes["positive_count"] == 1
        assert attitudes["negative_count"] == 1
        assert attitudes["difference"] == 0
        assert attitudes["user_attitude"] == "POSITIVE"

    def test_get_answer_with_attitude_stats_negative_user(self):
        self.client.post(
            f"/questions/{self.question_id}/answers/{self.answer_id}/attitudes",
            headers=self.aux_headers,
            json={"attitude_type": "POSITIVE"},
        )
        self.client.post(
            f"/questions/{self.question_id}/answers/{self.answer_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "NEGATIVE"},
        )
        response = self.client.get(
            f"/questions/{self.question_id}/answers/{self.answer_id}",
            headers=self.headers,
        )
        assert response.status_code == 200
        attitudes = response.json()["data"]["answer"]["attitudes"]
        assert attitudes["positive_count"] == 1
        assert attitudes["negative_count"] == 1
        assert attitudes["difference"] == 0
        assert attitudes["user_attitude"] == "NEGATIVE"

    def test_set_undefined_attitude_clears_positive(self):
        self.client.post(
            f"/questions/{self.question_id}/answers/{self.answer_id}/attitudes",
            headers=self.aux_headers,
            json={"attitude_type": "POSITIVE"},
        )
        response = self.client.post(
            f"/questions/{self.question_id}/answers/{self.answer_id}/attitudes",
            headers=self.aux_headers,
            json={"attitude_type": "UNDEFINED"},
        )
        assert response.status_code in (200, 201)
        data = response.json()
        assert data["data"]["attitudes"]["positive_count"] == 0
        assert data["data"]["attitudes"]["negative_count"] == 0
        assert data["data"]["attitudes"]["difference"] == 0
        assert data["data"]["attitudes"]["user_attitude"] == "UNDEFINED"

    def test_set_undefined_clears_negative(self):
        response = self.client.post(
            f"/questions/{self.question_id}/answers/{self.answer_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "UNDEFINED"},
        )
        assert response.status_code in (200, 201)
        data = response.json()
        assert data["data"]["attitudes"]["positive_count"] == 0
        assert data["data"]["attitudes"]["negative_count"] == 0
        assert data["data"]["attitudes"]["difference"] == 0
        assert data["data"]["attitudes"]["user_attitude"] == "UNDEFINED"

    def test_get_answer_after_clearing_attitudes_user1(self):
        response = self.client.get(
            f"/questions/{self.question_id}/answers/{self.answer_id}",
            headers=self.headers,
        )
        assert response.status_code == 200
        attitudes = response.json()["data"]["answer"]["attitudes"]
        assert attitudes["positive_count"] == 0
        assert attitudes["negative_count"] == 0
        assert attitudes["difference"] == 0
        assert attitudes["user_attitude"] == "UNDEFINED"

    def test_get_answer_after_clearing_attitudes_user2(self):
        response = self.client.get(
            f"/questions/{self.question_id}/answers/{self.answer_id}",
            headers=self.aux_headers,
        )
        assert response.status_code == 200
        attitudes = response.json()["data"]["answer"]["attitudes"]
        assert attitudes["positive_count"] == 0
        assert attitudes["negative_count"] == 0
        assert attitudes["difference"] == 0
        assert attitudes["user_attitude"] == "UNDEFINED"

    def test_get_answer_final_check_user1(self):
        response = self.client.get(
            f"/questions/{self.question_id}/answers/{self.answer_id}",
            headers=self.headers,
        )
        assert response.status_code == 200
        attitudes = response.json()["data"]["answer"]["attitudes"]
        assert attitudes["positive_count"] == 0
        assert attitudes["negative_count"] == 0
        assert attitudes["difference"] == 0
        assert attitudes["user_attitude"] == "UNDEFINED"
