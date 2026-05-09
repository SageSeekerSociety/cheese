"""
Integration tests for the Comments module.
Migrated from cheese-backend/test/comment.e2e-spec.ts (624 lines, 33 tests)
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


class TestCommentsCreateIntegration:
    """Tests for creating comments."""

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
        self.test_prefix = f"C{unique_int(100000, 999999)}"
        topic_name = f"Topic_{unique_int(100000, 999999)}"
        self.topic_id = create_topic_in_db(self.db, self.portal, topic_name, self.user.user_id)
        self.comment_ids: list[int] = []
        self.question_ids: list[int] = []
        for i in range(4):
            resp = self.client.post(
                "/questions",
                headers=self.headers,
                json={
                    "title": f"{self.test_prefix} Question {i}",
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

    def test_create_comment_on_question(self):
        response = self.client.post(
            f"/comments/question/{self.question_ids[0]}",
            headers=self.headers,
            json={"content": f"{self.test_prefix} zfgg好帅"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == 201
        assert data["data"]["id"] is not None
        self.comment_ids.append(data["data"]["id"])

    def test_create_multiple_comments(self):
        contents = [
            "zfggnb",
            "zfgg???????",
            "宵宫!",
        ]
        for i, content in enumerate(contents):
            response = self.client.post(
                f"/comments/question/{self.question_ids[i + 1]}",
                headers=self.headers,
                json={"content": f"{self.test_prefix} {content}"},
            )
            assert response.status_code == 201
            self.comment_ids.append(response.json()["data"]["id"])

    def test_create_nested_comment(self):
        parent_resp = self.client.post(
            f"/comments/question/{self.question_ids[0]}",
            headers=self.headers,
            json={"content": f"{self.test_prefix} Parent comment"},
        )
        parent_id = parent_resp.json()["data"]["id"]
        response = self.client.post(
            f"/comments/comment/{parent_id}",
            headers=self.headers,
            json={"content": f"{self.test_prefix} 啦啦啦德玛西亚"},
        )
        assert response.status_code == 201

    def test_create_deeply_nested_comment(self):
        parent_resp = self.client.post(
            f"/comments/question/{self.question_ids[0]}",
            headers=self.headers,
            json={"content": f"{self.test_prefix} Level 1"},
        )
        parent_id = parent_resp.json()["data"]["id"]
        child_resp = self.client.post(
            f"/comments/comment/{parent_id}",
            headers=self.headers,
            json={"content": f"{self.test_prefix} 你猜我是谁"},
        )
        child_id = child_resp.json()["data"]["id"]
        response = self.client.post(
            f"/comments/comment/{child_id}",
            headers=self.headers,
            json={"content": f"{self.test_prefix} 滚啊，我怎么知道你是谁"},
        )
        assert response.status_code == 201

    def test_create_comment_invalid_commentable_id(self):
        response = self.client.post(
            "/comments/question/114514",
            headers=self.headers,
            json={"content": f"{self.test_prefix} what you gonna to know?"},
        )
        assert response.status_code == 404

    def test_create_comment_no_auth(self):
        response = self.client.post(
            f"/comments/question/{self.question_ids[0]}",
            json={"content": f"{self.test_prefix} what you gonna to know?"},
        )
        assert response.status_code == 401


class TestCommentsGetIntegration:
    """Tests for getting comments."""

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
        self.test_prefix = f"C{unique_int(100000, 999999)}"
        topic_name = f"Topic_{unique_int(100000, 999999)}"
        self.topic_id = create_topic_in_db(self.db, self.portal, topic_name, self.user.user_id)
        q_resp = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.test_prefix} Get Comment Test",
                "content": "Test content",
                "type": 0,
                "topics": [self.topic_id],
            },
        )
        self.question_id = q_resp.json()["data"]["id"]
        c_resp = self.client.post(
            f"/comments/question/{self.question_id}",
            headers=self.headers,
            json={"content": f"{self.test_prefix} zfgg好帅"},
        )
        self.comment_id = c_resp.json()["data"]["id"]
        nested_resp = self.client.post(
            f"/comments/comment/{self.comment_id}",
            headers=self.headers,
            json={"content": f"{self.test_prefix} 啦啦啦德玛西亚"},
        )
        self.nested_comment_id = nested_resp.json()["data"]["id"]
        self.aux_user, self.aux_headers = self._create_aux_user()
        self._get_user_dto()

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def _get_user_dto(self):
        resp = self.client.get(f"/users/{self.user.user_id}", headers=self.headers)
        self.user_dto = resp.json()["data"]["user"]

    def test_get_comment_by_id_question_comment(self):
        response = self.client.get(
            f"/comments/{self.comment_id}",
            headers=self.aux_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["comment"]["id"] is not None
        assert data["data"]["comment"]["commentable_id"] == self.question_id
        assert data["data"]["comment"]["commentable_type"] == "QUESTION"
        assert "zfgg好帅" in data["data"]["comment"]["content"]
        assert data["data"]["comment"]["user"] == self.user_dto
        assert "created_at" in data["data"]["comment"]
        assert data["data"]["comment"]["attitudes"]["positive_count"] == 0
        assert data["data"]["comment"]["attitudes"]["negative_count"] == 0
        assert data["data"]["comment"]["attitudes"]["difference"] == 0
        assert data["data"]["comment"]["attitudes"]["user_attitude"] == "UNDEFINED"

    def test_get_comment_by_id_nested_comment(self):
        response = self.client.get(
            f"/comments/{self.nested_comment_id}",
            headers=self.aux_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["comment"]["id"] is not None
        assert data["data"]["comment"]["commentable_id"] == self.comment_id
        assert data["data"]["comment"]["commentable_type"] == "COMMENT"
        assert "啦啦啦德玛西亚" in data["data"]["comment"]["content"]
        assert data["data"]["comment"]["user"] == self.user_dto
        assert "created_at" in data["data"]["comment"]
        assert data["data"]["comment"]["attitudes"]["positive_count"] == 0
        assert data["data"]["comment"]["attitudes"]["negative_count"] == 0
        assert data["data"]["comment"]["attitudes"]["difference"] == 0
        assert data["data"]["comment"]["attitudes"]["user_attitude"] == "UNDEFINED"

    def test_get_comment_not_found(self):
        response = self.client.get(
            "/comments/114514",
            headers=self.aux_headers,
        )
        assert response.status_code == 404

    def test_get_comment_no_auth(self):
        response = self.client.get(f"/comments/{self.comment_id}")
        assert response.status_code == 401


class TestCommentsAttitudeIntegration:
    """Tests for comment attitudes."""

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
        self.test_prefix = f"C{unique_int(100000, 999999)}"
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
        c_resp = self.client.post(
            f"/comments/question/{self.question_id}",
            headers=self.headers,
            json={"content": f"{self.test_prefix} zfgg好帅"},
        )
        self.comment_id = c_resp.json()["data"]["id"]
        nested_resp = self.client.post(
            f"/comments/comment/{self.comment_id}",
            headers=self.headers,
            json={"content": f"{self.test_prefix} 啦啦啦德玛西亚"},
        )
        self.nested_comment_id = nested_resp.json()["data"]["id"]
        self.aux_user, self.aux_headers = self._create_aux_user()
        self._get_user_dto()

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def _get_user_dto(self):
        resp = self.client.get(f"/users/{self.user.user_id}", headers=self.headers)
        self.user_dto = resp.json()["data"]["user"]

    def test_agree_to_comment(self):
        response = self.client.post(
            f"/comments/{self.comment_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "POSITIVE"},
        )
        assert response.status_code in (200, 201)

    def test_agree_to_nested_comment(self):
        response = self.client.post(
            f"/comments/{self.nested_comment_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "POSITIVE"},
        )
        assert response.status_code in (200, 201)

    def test_get_comment_attitude_from_others(self):
        self.client.post(
            f"/comments/{self.comment_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "POSITIVE"},
        )
        response = self.client.get(
            f"/comments/{self.comment_id}",
            headers=self.aux_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["comment"]["commentable_id"] == self.question_id
        assert data["data"]["comment"]["commentable_type"] == "QUESTION"
        assert "zfgg好帅" in data["data"]["comment"]["content"]
        assert data["data"]["comment"]["user"] == self.user_dto
        assert data["data"]["comment"]["attitudes"]["positive_count"] == 1
        assert data["data"]["comment"]["attitudes"]["negative_count"] == 0
        assert data["data"]["comment"]["attitudes"]["difference"] == 1
        assert data["data"]["comment"]["attitudes"]["user_attitude"] == "UNDEFINED"

    def test_get_comment_attitude_from_self(self):
        self.client.post(
            f"/comments/{self.comment_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "POSITIVE"},
        )
        response = self.client.get(
            f"/comments/{self.comment_id}",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["comment"]["attitudes"]["positive_count"] == 1
        assert data["data"]["comment"]["attitudes"]["negative_count"] == 0
        assert data["data"]["comment"]["attitudes"]["difference"] == 1
        assert data["data"]["comment"]["attitudes"]["user_attitude"] == "POSITIVE"

    def test_disagree_to_comment(self):
        self.client.post(
            f"/comments/{self.comment_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "POSITIVE"},
        )
        response = self.client.post(
            f"/comments/{self.comment_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "NEGATIVE"},
        )
        assert response.status_code in (200, 201)

    def test_get_negative_attitude_from_others(self):
        self.client.post(
            f"/comments/{self.comment_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "NEGATIVE"},
        )
        response = self.client.get(
            f"/comments/{self.comment_id}",
            headers=self.aux_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["comment"]["attitudes"]["positive_count"] == 0
        assert data["data"]["comment"]["attitudes"]["negative_count"] == 1
        assert data["data"]["comment"]["attitudes"]["difference"] == -1
        assert data["data"]["comment"]["attitudes"]["user_attitude"] == "UNDEFINED"

    def test_get_negative_attitude_from_self(self):
        self.client.post(
            f"/comments/{self.comment_id}/attitudes",
            headers=self.headers,
            json={"attitude_type": "NEGATIVE"},
        )
        response = self.client.get(
            f"/comments/{self.comment_id}",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["comment"]["attitudes"]["positive_count"] == 0
        assert data["data"]["comment"]["attitudes"]["negative_count"] == 1
        assert data["data"]["comment"]["attitudes"]["difference"] == -1
        assert data["data"]["comment"]["attitudes"]["user_attitude"] == "NEGATIVE"

    def test_attitude_comment_not_found(self):
        response = self.client.post(
            "/comments/114514/attitudes",
            headers=self.headers,
            json={"attitude_type": "POSITIVE"},
        )
        assert response.status_code == 404

    def test_attitude_no_auth(self):
        response = self.client.post(
            f"/comments/{self.comment_id}/attitudes",
            json={"attitude_type": "NEGATIVE"},
        )
        assert response.status_code == 401


class TestCommentsDeleteIntegration:
    """Tests for deleting comments."""

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
        self.test_prefix = f"C{unique_int(100000, 999999)}"
        topic_name = f"Topic_{unique_int(100000, 999999)}"
        self.topic_id = create_topic_in_db(self.db, self.portal, topic_name, self.user.user_id)
        q_resp = self.client.post(
            "/questions",
            headers=self.headers,
            json={
                "title": f"{self.test_prefix} Delete Test",
                "content": "Delete test content",
                "type": 0,
                "topics": [self.topic_id],
            },
        )
        self.question_id = q_resp.json()["data"]["id"]
        self.comment_ids: list[int] = []
        for i in range(3):
            c_resp = self.client.post(
                f"/comments/question/{self.question_id}",
                headers=self.headers,
                json={"content": f"{self.test_prefix} Comment {i}"},
            )
            self.comment_ids.append(c_resp.json()["data"]["id"])
        self.aux_user, self.aux_headers = self._create_aux_user()

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_delete_comment(self):
        response = self.client.delete(
            f"/comments/{self.comment_ids[1]}",
            headers=self.headers,
        )
        assert response.status_code == 200

    def test_delete_comment_not_owner(self):
        response = self.client.delete(
            f"/comments/{self.comment_ids[0]}",
            headers=self.aux_headers,
        )
        assert response.status_code == 403

    def test_delete_comment_not_found(self):
        response = self.client.delete(
            "/comments/114514",
            headers=self.aux_headers,
        )
        assert response.status_code == 404

    def test_delete_comment_no_auth(self):
        response = self.client.delete(
            f"/comments/{self.comment_ids[0]}",
        )
        assert response.status_code == 401

    def test_delete_nested_comment(self):
        nested_resp = self.client.post(
            f"/comments/comment/{self.comment_ids[0]}",
            headers=self.headers,
            json={"content": f"{self.test_prefix} Nested to delete"},
        )
        nested_id = nested_resp.json()["data"]["id"]
        response = self.client.delete(
            f"/comments/{nested_id}",
            headers=self.headers,
        )
        assert response.status_code == 200


class TestCommentsUpdateIntegration:
    """Tests for updating comments."""

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
        self.test_prefix = f"C{unique_int(100000, 999999)}"
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
        c_resp = self.client.post(
            f"/comments/question/{self.question_id}",
            headers=self.headers,
            json={"content": f"{self.test_prefix} 宵宫!"},
        )
        self.comment_id = c_resp.json()["data"]["id"]
        self._get_user_dto()

    def _get_user_dto(self):
        resp = self.client.get(f"/users/{self.user.user_id}", headers=self.headers)
        self.user_dto = resp.json()["data"]["user"]

    def test_update_comment_not_found(self):
        deleted_resp = self.client.post(
            f"/comments/question/{self.question_id}",
            headers=self.headers,
            json={"content": f"{self.test_prefix} To delete"},
        )
        deleted_id = deleted_resp.json()["data"]["id"]
        self.client.delete(f"/comments/{deleted_id}", headers=self.headers)
        response = self.client.patch(
            f"/comments/{deleted_id}",
            headers=self.headers,
            json={"content": f"{self.test_prefix} 主播，你怎么不说话了"},
        )
        assert response.status_code == 404

    def test_update_comment(self):
        response = self.client.patch(
            f"/comments/{self.comment_id}",
            headers=self.headers,
            json={"content": f"{self.test_prefix} 我超，宵宫!"},
        )
        assert response.status_code == 200

        get_resp = self.client.get(
            f"/comments/{self.comment_id}",
            headers=self.headers,
        )
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["data"]["comment"]["commentable_id"] == self.question_id
        assert data["data"]["comment"]["commentable_type"] == "QUESTION"
        assert "我超，宵宫!" in data["data"]["comment"]["content"]
        assert data["data"]["comment"]["user"] == self.user_dto
        assert data["data"]["comment"]["attitudes"]["positive_count"] == 0
        assert data["data"]["comment"]["attitudes"]["negative_count"] == 0
        assert data["data"]["comment"]["attitudes"]["difference"] == 0
        assert data["data"]["comment"]["attitudes"]["user_attitude"] == "UNDEFINED"

    def test_update_comment_no_auth(self):
        response = self.client.patch(
            f"/comments/{self.comment_id}",
            json={"content": f"{self.test_prefix} 我超，宵宫!"},
        )
        assert response.status_code == 401

    def test_get_updated_comment_with_tag(self):
        self.client.patch(
            f"/comments/{self.comment_id}",
            headers=self.headers,
            json={"content": f"{self.test_prefix} Updated 宵宫!"},
        )
        response = self.client.get(
            f"/comments/{self.comment_id}",
            headers=self.headers,
            params={"tag": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["comment"]["commentable_id"] == self.question_id
        assert data["data"]["comment"]["commentable_type"] == "QUESTION"
        assert "宵宫!" in data["data"]["comment"]["content"]
        assert data["data"]["comment"]["user"] == self.user_dto
        assert data["data"]["comment"]["attitudes"]["positive_count"] == 0
        assert data["data"]["comment"]["attitudes"]["negative_count"] == 0
        assert data["data"]["comment"]["attitudes"]["difference"] == 0
        assert data["data"]["comment"]["attitudes"]["user_attitude"] == "UNDEFINED"
