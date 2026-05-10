"""
Integration tests for the Groups module.
Migrated from cheese-backend/test/groups.e2e-spec.ts (783 lines, 35 tests)
Complete equivalence migration.
"""

from datetime import UTC, datetime

import pytest
from anyio.from_thread import BlockingPortal
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.avatars.models import Avatar
from tests.integration.conftest import CreatedUser, UserCreator, unique_int


def create_avatar_in_db(db_session: AsyncSession, portal: BlockingPortal) -> int:
    avatar = Avatar(
        url=f"/test/avatar_{unique_int(1000, 9999)}.jpg",
        name="test_avatar",
        created_at=datetime.now(UTC),
        avatar_type="upload",
        usage_count=0,
    )

    async def _do() -> int:
        db_session.add(avatar)
        await db_session.flush()
        return avatar.id

    return portal.call(_do)


class TestGroupsCreateIntegration:
    """Tests for creating groups."""

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
        self.group_prefix = f"G{unique_int(100000, 999999)}"
        self.pre_avatar_id = create_avatar_in_db(
            self.db,
            self.portal,
        )
        self.update_avatar_id = create_avatar_in_db(
            self.db,
            self.portal,
        )
        self.group_ids: list[int] = []
        self._get_user_dto()

    def _get_user_dto(self):
        resp = self.client.get(f"/users/{self.user.user_id}", headers=self.headers)
        self.user_dto = resp.json()["data"]["user"]

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_create_group(self):
        response = self.client.post(
            "/groups",
            headers=self.headers,
            json={
                "name": f"{self.group_prefix}数学之神膜膜喵",
                "intro": "不如原神",
                "avatarId": self.pre_avatar_id,
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == 201
        group_dto = data["data"]["group"]
        assert group_dto["id"] is not None
        assert "数学之神膜膜喵" in group_dto["name"]
        assert group_dto["avatarId"] == self.pre_avatar_id
        assert group_dto["owner"]["id"] == self.user.user_id
        assert "created_at" in group_dto
        assert "updated_at" in group_dto
        assert group_dto["member_count"] == 1
        assert group_dto["question_count"] == 0
        assert group_dto["answer_count"] == 0
        assert group_dto["is_member"] is True
        assert group_dto["is_owner"] is True
        assert group_dto["is_public"] is True
        assert group_dto["intro"] == "不如原神"
        self.group_ids.append(group_dto["id"])

    def test_create_multiple_groups(self):
        groups_data = [
            ("数学之神膜膜喵", "不如原神"),
            ("ICS膜膜膜", "pwb txdy!"),
            ("嘉然今天学什么", "学, 学个屁!"),
            ("XCPC启动", "启不动了"),
        ]
        for name, intro in groups_data:
            response = self.client.post(
                "/groups",
                headers=self.headers,
                json={
                    "name": f"{self.group_prefix}{name}",
                    "intro": intro,
                    "avatarId": self.pre_avatar_id,
                },
            )
            assert response.status_code == 201
            data = response.json()
            assert data["code"] == 201
            group_dto = data["data"]["group"]
            assert group_dto["id"] is not None
            assert name in group_dto["name"]
            assert group_dto["avatarId"] == self.pre_avatar_id
            assert group_dto["member_count"] == 1
            assert group_dto["is_member"] is True
            assert group_dto["is_owner"] is True
            self.group_ids.append(group_dto["id"])
        assert len(self.group_ids) == 4

    def test_create_group_no_auth(self):
        response = self.client.post(
            "/groups",
            json={
                "name": f"{self.group_prefix}_NoAuth",
                "intro": "test",
                "avatarId": self.pre_avatar_id,
            },
        )
        assert response.status_code == 401


class TestGroupsGetIntegration:
    """Tests for getting groups."""

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
        self.group_prefix = f"G{unique_int(100000, 999999)}"
        self.pre_avatar_id = create_avatar_in_db(
            self.db,
            self.portal,
        )
        self._get_user_dto()
        resp = self.client.post(
            "/groups",
            headers=self.headers,
            json={
                "name": f"{self.group_prefix}数学之神膜膜喵",
                "intro": "不如原神",
                "avatarId": self.pre_avatar_id,
            },
        )
        self.group_id = resp.json()["data"]["group"]["id"]
        self.aux_user, self.aux_headers = self._create_aux_user()

    def _get_user_dto(self):
        resp = self.client.get(f"/users/{self.user.user_id}", headers=self.headers)
        self.user_dto = resp.json()["data"]["user"]

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_get_group(self):
        response = self.client.get(
            f"/groups/{self.group_id}",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        group_dto = data["data"]["group"]
        assert group_dto["id"] == self.group_id
        assert "数学之神膜膜喵" in group_dto["name"]
        assert group_dto["intro"] == "不如原神"
        assert group_dto["avatarId"] == self.pre_avatar_id
        assert group_dto["owner"]["id"] == self.user.user_id
        assert "created_at" in group_dto
        assert "updated_at" in group_dto
        assert group_dto["member_count"] == 1
        assert group_dto["question_count"] == 0
        assert group_dto["answer_count"] == 0
        assert group_dto["is_member"] is True
        assert group_dto["is_owner"] is True

    def test_get_group_for_another_user(self):
        response = self.client.get(
            f"/groups/{self.group_id}",
            headers=self.aux_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        group_dto = data["data"]["group"]
        assert group_dto["id"] == self.group_id
        assert "数学之神膜膜喵" in group_dto["name"]
        assert group_dto["intro"] == "不如原神"
        assert group_dto["avatarId"] == self.pre_avatar_id
        assert group_dto["owner"]["id"] == self.user.user_id
        assert "created_at" in group_dto
        assert "updated_at" in group_dto
        assert group_dto["member_count"] == 1
        assert group_dto["question_count"] == 0
        assert group_dto["answer_count"] == 0
        assert group_dto["is_member"] is False
        assert group_dto["is_owner"] is False

    def test_get_group_not_found(self):
        response = self.client.get(
            "/groups/0",
            headers=self.headers,
        )
        assert response.status_code == 404


class TestGroupsJoinIntegration:
    """Tests for joining groups."""

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
        self.group_prefix = f"G{unique_int(100000, 999999)}"
        self.pre_avatar_id = create_avatar_in_db(
            self.db,
            self.portal,
        )
        self._get_user_dto()
        self.group_ids: list[int] = []
        for _i, (name, intro) in enumerate(
            [
                ("数学之神膜膜喵", "不如原神"),
                ("ICS膜膜膜", "pwb txdy!"),
            ]
        ):
            resp = self.client.post(
                "/groups",
                headers=self.headers,
                json={
                    "name": f"{self.group_prefix}{name}",
                    "intro": intro,
                    "avatarId": self.pre_avatar_id,
                },
            )
            self.group_ids.append(resp.json()["data"]["group"]["id"])
        self.aux_user, self.aux_headers = self._create_aux_user()

    def _get_user_dto(self):
        resp = self.client.get(f"/users/{self.user.user_id}", headers=self.headers)
        self.user_dto = resp.json()["data"]["user"]

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_join_group(self):
        for group_id in self.group_ids:
            response = self.client.post(
                f"/groups/{group_id}/members",
                headers=self.aux_headers,
                json={"intro": "我是初音未来"},
            )
            assert response.status_code == 201
            data = response.json()
            assert data["code"] == 201

    def test_get_group_with_is_member_true(self):
        self.client.post(
            f"/groups/{self.group_ids[0]}/members",
            headers=self.aux_headers,
            json={"intro": "我是初音未来"},
        )
        response = self.client.get(
            f"/groups/{self.group_ids[0]}",
            headers=self.aux_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        group_dto = data["data"]["group"]
        assert group_dto["id"] == self.group_ids[0]
        assert "数学之神膜膜喵" in group_dto["name"]
        assert group_dto["intro"] == "不如原神"
        assert group_dto["avatarId"] == self.pre_avatar_id
        assert group_dto["owner"]["id"] == self.user.user_id
        assert "created_at" in group_dto
        assert "updated_at" in group_dto
        assert group_dto["member_count"] == 2
        assert group_dto["question_count"] == 0
        assert group_dto["answer_count"] == 0
        assert group_dto["is_member"] is True
        assert group_dto["is_owner"] is False

    def test_join_group_not_found(self):
        response = self.client.post(
            "/groups/0/members",
            headers=self.aux_headers,
            json={"intro": "我是初音未来"},
        )
        assert response.status_code == 404

    def test_join_group_already_joined(self):
        self.client.post(
            f"/groups/{self.group_ids[0]}/members",
            headers=self.aux_headers,
            json={"intro": "我是初音未来"},
        )
        response = self.client.post(
            f"/groups/{self.group_ids[0]}/members",
            headers=self.aux_headers,
            json={"intro": "我是初音未来"},
        )
        assert response.status_code == 409


class TestGroupsUpdateIntegration:
    """Tests for updating groups."""

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
        self.group_prefix = f"G{unique_int(100000, 999999)}"
        self.pre_avatar_id = create_avatar_in_db(
            self.db,
            self.portal,
        )
        self.update_avatar_id = create_avatar_in_db(
            self.db,
            self.portal,
        )
        self._get_user_dto()
        self.group_ids: list[int] = []
        for name, intro in [
            ("数学之神膜膜喵", "不如原神"),
            ("ICS膜膜膜", "pwb txdy!"),
        ]:
            resp = self.client.post(
                "/groups",
                headers=self.headers,
                json={
                    "name": f"{self.group_prefix}{name}",
                    "intro": intro,
                    "avatarId": self.pre_avatar_id,
                },
            )
            self.group_ids.append(resp.json()["data"]["group"]["id"])
        self.aux_user, self.aux_headers = self._create_aux_user()
        self.client.post(
            f"/groups/{self.group_ids[0]}/members",
            headers=self.aux_headers,
            json={"intro": "我是初音未来"},
        )

    def _get_user_dto(self):
        resp = self.client.get(f"/users/{self.user.user_id}", headers=self.headers)
        self.user_dto = resp.json()["data"]["user"]

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_update_group(self):
        response = self.client.put(
            f"/groups/{self.group_ids[0]}",
            headers=self.headers,
            json={
                "name": f"{self.group_prefix}关注幻城谢谢喵",
                "intro": "湾原审万德",
                "avatarId": self.update_avatar_id,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200

    def test_get_updated_group_from_another_user(self):
        self.client.put(
            f"/groups/{self.group_ids[0]}",
            headers=self.headers,
            json={
                "name": f"{self.group_prefix}关注幻城谢谢喵",
                "intro": "湾原审万德",
                "avatarId": self.update_avatar_id,
            },
        )
        response = self.client.get(
            f"/groups/{self.group_ids[0]}",
            headers=self.aux_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        group_dto = data["data"]["group"]
        assert group_dto["id"] == self.group_ids[0]
        assert "关注幻城谢谢喵" in group_dto["name"]
        assert group_dto["intro"] == "湾原审万德"
        assert group_dto["avatarId"] == self.update_avatar_id
        assert group_dto["owner"]["id"] == self.user.user_id
        assert "created_at" in group_dto
        assert "updated_at" in group_dto
        assert group_dto["member_count"] == 2
        assert group_dto["question_count"] == 0
        assert group_dto["answer_count"] == 0
        assert group_dto["is_member"] is True
        assert group_dto["is_owner"] is False

    def test_update_group_not_found(self):
        response = self.client.put(
            "/groups/0",
            headers=self.headers,
            json={
                "name": f"{self.group_prefix}关注幻城谢谢喵",
                "intro": "湾原审万德",
                "avatarId": self.update_avatar_id,
            },
        )
        assert response.status_code == 404

    def test_update_group_name_already_used(self):
        response = self.client.put(
            f"/groups/{self.group_ids[0]}",
            headers=self.headers,
            json={
                "name": f"{self.group_prefix}ICS膜膜膜",
                "intro": "湾原审万德",
                "avatarId": self.update_avatar_id,
            },
        )
        assert response.status_code == 409

    def test_update_group_not_owner(self):
        response = self.client.put(
            f"/groups/{self.group_ids[0]}",
            headers=self.aux_headers,
            json={
                "name": f"{self.group_prefix}关注幻城谢谢喵",
                "intro": "湾原审万德",
                "avatarId": self.update_avatar_id,
            },
        )
        assert response.status_code == 403


class TestGroupsLeaveIntegration:
    """Tests for leaving groups."""

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
        self.group_prefix = f"G{unique_int(100000, 999999)}"
        self.pre_avatar_id = create_avatar_in_db(
            self.db,
            self.portal,
        )
        self.update_avatar_id = create_avatar_in_db(
            self.db,
            self.portal,
        )
        self._get_user_dto()
        resp = self.client.post(
            "/groups",
            headers=self.headers,
            json={
                "name": f"{self.group_prefix}关注幻城谢谢喵",
                "intro": "湾原审万德",
                "avatarId": self.update_avatar_id,
            },
        )
        self.group_id = resp.json()["data"]["group"]["id"]
        self.aux_user, self.aux_headers = self._create_aux_user()
        self.client.post(
            f"/groups/{self.group_id}/members",
            headers=self.aux_headers,
            json={"intro": "我是初音未来"},
        )

    def _get_user_dto(self):
        resp = self.client.get(f"/users/{self.user.user_id}", headers=self.headers)
        self.user_dto = resp.json()["data"]["user"]

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_leave_group(self):
        response = self.client.delete(
            f"/groups/{self.group_id}/members",
            headers=self.aux_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200

    def test_get_group_with_is_member_false_after_leave(self):
        self.client.delete(
            f"/groups/{self.group_id}/members",
            headers=self.aux_headers,
        )
        response = self.client.get(
            f"/groups/{self.group_id}",
            headers=self.aux_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        group_dto = data["data"]["group"]
        assert group_dto["id"] == self.group_id
        assert "关注幻城谢谢喵" in group_dto["name"]
        assert group_dto["intro"] == "湾原审万德"
        assert group_dto["avatarId"] == self.update_avatar_id
        assert group_dto["owner"]["id"] == self.user.user_id
        assert "created_at" in group_dto
        assert "updated_at" in group_dto
        assert group_dto["member_count"] == 1
        assert group_dto["question_count"] == 0
        assert group_dto["answer_count"] == 0
        assert group_dto["is_member"] is False
        assert group_dto["is_owner"] is False

    def test_leave_group_not_found(self):
        response = self.client.delete(
            "/groups/0/members",
            headers=self.aux_headers,
        )
        assert response.status_code == 404

    def test_leave_group_not_joined(self):
        self.client.delete(
            f"/groups/{self.group_id}/members",
            headers=self.aux_headers,
        )
        response = self.client.delete(
            f"/groups/{self.group_id}/members",
            headers=self.aux_headers,
        )
        assert response.status_code == 409


class TestGroupsDeleteIntegration:
    """Tests for deleting groups."""

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
        self.group_prefix = f"G{unique_int(100000, 999999)}"
        self.avatar_id = create_avatar_in_db(
            self.db,
            self.portal,
        )
        self.group_ids: list[int] = []
        for name, intro in [
            ("XCPC启动", "启不动了"),
            ("ICS膜膜膜", "pwb txdy!"),
        ]:
            resp = self.client.post(
                "/groups",
                headers=self.headers,
                json={
                    "name": f"{self.group_prefix}{name}",
                    "intro": intro,
                    "avatarId": self.avatar_id,
                },
            )
            self.group_ids.append(resp.json()["data"]["group"]["id"])
        self.aux_user, self.aux_headers = self._create_aux_user()
        self.client.post(
            f"/groups/{self.group_ids[1]}/members",
            headers=self.aux_headers,
            json={"intro": "我是初音未来"},
        )

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_delete_group(self):
        response = self.client.delete(
            f"/groups/{self.group_ids[0]}",
            headers=self.headers,
        )
        assert response.status_code in (200, 204)

    def test_get_group_not_found_after_deletion(self):
        self.client.delete(
            f"/groups/{self.group_ids[0]}",
            headers=self.headers,
        )
        response = self.client.get(
            f"/groups/{self.group_ids[0]}",
            headers=self.headers,
        )
        assert response.status_code == 404

    def test_delete_group_not_found(self):
        response = self.client.delete(
            "/groups/0",
            headers=self.headers,
        )
        assert response.status_code == 404

    def test_delete_group_not_owner(self):
        response = self.client.delete(
            f"/groups/{self.group_ids[1]}",
            headers=self.aux_headers,
        )
        assert response.status_code == 403


class TestGroupsMembersIntegration:
    """Tests for getting group members."""

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
        self.group_prefix = f"G{unique_int(100000, 999999)}"
        self.avatar_id = create_avatar_in_db(
            self.db,
            self.portal,
        )
        self._get_user_dto()
        self.group_ids: list[int] = []
        for name, intro in [
            ("关注幻城谢谢喵", "湾原审万德"),
            ("ICS膜膜膜", "pwb txdy!"),
        ]:
            resp = self.client.post(
                "/groups",
                headers=self.headers,
                json={
                    "name": f"{self.group_prefix}{name}",
                    "intro": intro,
                    "avatarId": self.avatar_id,
                },
            )
            self.group_ids.append(resp.json()["data"]["group"]["id"])
        self.aux_user, self.aux_headers = self._create_aux_user()
        self._get_aux_user_dto()
        self.client.post(
            f"/groups/{self.group_ids[0]}/members",
            headers=self.aux_headers,
            json={"intro": "我是初音未来"},
        )
        self.client.post(
            f"/groups/{self.group_ids[1]}/members",
            headers=self.aux_headers,
            json={"intro": "我是初音未来"},
        )
        self.client.delete(
            f"/groups/{self.group_ids[0]}/members",
            headers=self.aux_headers,
        )

    def _get_user_dto(self):
        resp = self.client.get(f"/users/{self.user.user_id}", headers=self.headers)
        self.user_dto = resp.json()["data"]["user"]

    def _get_aux_user_dto(self):
        resp = self.client.get(f"/users/{self.aux_user.user_id}", headers=self.headers)
        self.aux_user_dto = resp.json()["data"]["user"]

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_get_group_members(self):
        response = self.client.get(
            f"/groups/{self.group_ids[1]}/members",
            headers=self.headers,
            params={"page_size": 1},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["members"]) == 1
        assert data["data"]["members"][0]["id"] == self.user.user_id
        assert data["data"]["page"]["pageStart"] == self.user.user_id
        assert data["data"]["page"]["pageSize"] == 1
        assert data["data"]["page"]["hasPrev"] is False
        assert data["data"]["page"]["prevStart"] == 0
        assert data["data"]["page"]["hasMore"] is True
        assert data["data"]["page"]["nextStart"] == self.aux_user.user_id

    def test_get_group_members_from_specific_user(self):
        response = self.client.get(
            f"/groups/{self.group_ids[1]}/members",
            headers=self.headers,
            params={"page_size": 1, "page_start": self.aux_user.user_id},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["members"]) == 1
        assert data["data"]["members"][0]["id"] == self.aux_user.user_id
        assert data["data"]["page"]["pageStart"] == self.aux_user.user_id
        assert data["data"]["page"]["pageSize"] == 1
        assert data["data"]["page"]["hasPrev"] is True
        assert data["data"]["page"]["prevStart"] == self.user.user_id
        assert data["data"]["page"]["hasMore"] is False
        assert data["data"]["page"]["nextStart"] == 0

    def test_get_group_members_from_quit_user(self):
        response = self.client.get(
            f"/groups/{self.group_ids[0]}/members",
            headers=self.headers,
            params={"page_size": 1, "page_start": self.aux_user.user_id},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["members"]) == 0
        assert data["data"]["page"]["pageStart"] == 0
        assert data["data"]["page"]["pageSize"] == 0
        assert data["data"]["page"]["hasPrev"] is True
        assert data["data"]["page"]["prevStart"] == self.user.user_id
        assert data["data"]["page"]["hasMore"] is False
        assert data["data"]["page"]["nextStart"] == 0

    def test_get_group_members_for_another_user(self):
        response = self.client.get(
            f"/groups/{self.group_ids[1]}/members",
            headers=self.aux_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["members"]) == 2
        assert data["data"]["members"][0]["id"] == self.user.user_id
        assert data["data"]["members"][1]["id"] == self.aux_user.user_id
        assert data["data"]["page"]["pageStart"] == self.user.user_id
        assert data["data"]["page"]["pageSize"] == 2
        assert data["data"]["page"]["hasPrev"] is False
        assert data["data"]["page"]["prevStart"] == 0
        assert data["data"]["page"]["hasMore"] is False
        assert data["data"]["page"]["nextStart"] == 0

    def test_get_group_members_not_found(self):
        response = self.client.get(
            "/groups/0/members",
            headers=self.headers,
        )
        assert response.status_code == 404

    def test_get_group_members_negative_page_size(self):
        response = self.client.get(
            f"/groups/{self.group_ids[1]}/members",
            headers=self.headers,
            params={"page_size": -1},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["members"]) == 0
        assert data["data"]["page"]["pageStart"] == 0
        assert data["data"]["page"]["pageSize"] == 0
        assert data["data"]["page"]["hasPrev"] is False
        assert data["data"]["page"]["prevStart"] == 0
        assert data["data"]["page"]["hasMore"] is False
        assert data["data"]["page"]["nextStart"] == 0


class TestGroupTargetsIntegration:
    """Tests for group targets."""

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
        self.group_prefix = f"GT{unique_int(100000, 999999)}"
        self.avatar_id = create_avatar_in_db(
            self.db,
            self.portal,
        )

    def _create_group(self) -> int:
        response = self.client.post(
            "/groups",
            headers=self.headers,
            json={
                "name": f"{self.group_prefix}_{unique_int(1000, 9999)}",
                "intro": "Target test group",
                "avatarId": self.avatar_id,
            },
        )
        return response.json()["data"]["group"]["id"]

    def test_create_target(self):
        group_id = self._create_group()
        now = int(datetime.now(UTC).timestamp() * 1000)
        response = self.client.post(
            f"/groups/{group_id}/targets",
            headers=self.headers,
            json={
                "name": "Test Target",
                "intro": "Test target intro",
                "startedAt": now,
                "endedAt": now + 86400000,
                "attendanceFrequency": "DAILY",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == 201
        assert "id" in data["data"]

    def test_list_targets(self):
        group_id = self._create_group()
        now = int(datetime.now(UTC).timestamp() * 1000)
        self.client.post(
            f"/groups/{group_id}/targets",
            headers=self.headers,
            json={
                "name": "Target 1",
                "intro": "Intro 1",
                "startedAt": now,
                "endedAt": now + 86400000,
                "attendanceFrequency": "DAILY",
            },
        )
        self.client.post(
            f"/groups/{group_id}/targets",
            headers=self.headers,
            json={
                "name": "Target 2",
                "intro": "Intro 2",
                "startedAt": now,
                "endedAt": now + 86400000,
                "attendanceFrequency": "WEEKLY",
            },
        )

        response = self.client.get(f"/groups/{group_id}/targets", headers=self.headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["targets"]) >= 2

    def test_get_target(self):
        group_id = self._create_group()
        now = int(datetime.now(UTC).timestamp() * 1000)
        create_resp = self.client.post(
            f"/groups/{group_id}/targets",
            headers=self.headers,
            json={
                "name": "Get Target Test",
                "intro": "Get target intro",
                "startedAt": now,
                "endedAt": now + 86400000,
                "attendanceFrequency": "DAILY",
            },
        )
        target_id = create_resp.json()["data"]["id"]

        response = self.client.get(f"/groups/{group_id}/targets/{target_id}", headers=self.headers)
        assert response.status_code == 200
        target = response.json()["data"]["target"]
        assert target["name"] == "Get Target Test"

    def test_update_target(self):
        group_id = self._create_group()
        now = int(datetime.now(UTC).timestamp() * 1000)
        create_resp = self.client.post(
            f"/groups/{group_id}/targets",
            headers=self.headers,
            json={
                "name": "Before Update",
                "intro": "Before",
                "startedAt": now,
                "endedAt": now + 86400000,
                "attendanceFrequency": "DAILY",
            },
        )
        target_id = create_resp.json()["data"]["id"]

        response = self.client.put(
            f"/groups/{group_id}/targets/{target_id}",
            headers=self.headers,
            json={"name": "After Update", "intro": "After"},
        )
        assert response.status_code == 200
        target = response.json()["data"]["target"]
        assert target["name"] == "After Update"

    def test_delete_target(self):
        group_id = self._create_group()
        now = int(datetime.now(UTC).timestamp() * 1000)
        create_resp = self.client.post(
            f"/groups/{group_id}/targets",
            headers=self.headers,
            json={
                "name": "Delete Target",
                "intro": "Delete",
                "startedAt": now,
                "endedAt": now + 86400000,
                "attendanceFrequency": "DAILY",
            },
        )
        target_id = create_resp.json()["data"]["id"]

        response = self.client.delete(
            f"/groups/{group_id}/targets/{target_id}", headers=self.headers
        )
        assert response.status_code in (200, 204)

        response = self.client.get(f"/groups/{group_id}/targets/{target_id}", headers=self.headers)
        assert response.status_code == 404
