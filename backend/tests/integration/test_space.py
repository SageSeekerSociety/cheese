import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


class TestSpaceIntegration:
    @pytest.fixture
    def setup_space(self, user_client: UserCreator, api_client: TestClient) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)
        suffix = unique_int(10000000, 99999999)
        space_name = f"Test Space ({suffix})"
        resp = api_client.post(
            "/spaces",
            json={
                "name": space_name,
                "intro": "This is a test space.",
                "description": "A lengthy text. " * 100,
                "avatarId": 1,
                "enableRank": False,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 201
        space_id = resp.json()["data"]["space"]["id"]
        return {
            "creator": creator,
            "space_id": space_id,
            "space_name": space_name,
        }

    def test_get_space_not_found(self, user_client: UserCreator, api_client: TestClient):
        user = user_client.create_user()
        user.token = user_client.login(api_client, user.username, user.password)
        resp = api_client.get(
            "/spaces/999999999",
            headers={"Authorization": f"Bearer {user.token}"},
        )
        assert resp.status_code == 404
        error = resp.json().get("error", {})
        assert error.get("name") == "NotFoundError"

    def test_create_space(self, user_client: UserCreator, api_client: TestClient):
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)
        suffix = unique_int(10000000, 99999999)
        space_name = f"Test Space ({suffix})"
        resp = api_client.post(
            "/spaces",
            json={
                "name": space_name,
                "intro": "This is a test space.",
                "description": "A lengthy text. " * 100,
                "avatarId": 1,
                "enableRank": False,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 201
        data = resp.json()["data"]["space"]
        assert data["name"] == space_name
        assert data["intro"] == "This is a test space."
        assert data["enableRank"] is False

    def test_get_space_by_id(self, setup_space: dict, api_client: TestClient):
        creator = setup_space["creator"]
        space_id = setup_space["space_id"]
        space_name = setup_space["space_name"]
        resp = api_client.get(
            f"/spaces/{space_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]["space"]
        assert data["id"] == space_id
        assert data["name"] == space_name

    def test_create_space_with_existing_name_fails(self, setup_space: dict, api_client: TestClient):
        creator = setup_space["creator"]
        space_name = setup_space["space_name"]
        resp = api_client.post(
            "/spaces",
            json={
                "name": space_name,
                "intro": "Another space",
                "description": "Description",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 409, f"Expected 409 Conflict, got {resp.status_code}"

    def test_patch_space_with_empty_request(self, setup_space: dict, api_client: TestClient):
        creator = setup_space["creator"]
        space_id = setup_space["space_id"]
        resp = api_client.patch(
            f"/spaces/{space_id}",
            json={},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]["space"]
        assert data["id"] == space_id

    def test_patch_space_with_full_request(self, setup_space: dict, api_client: TestClient):
        creator = setup_space["creator"]
        space_id = setup_space["space_id"]
        updated_name = f"Updated Space ({unique_int(10000000, 99999999)})"
        updated_intro = "Updated intro"
        resp = api_client.patch(
            f"/spaces/{space_id}",
            json={
                "name": updated_name,
                "intro": updated_intro,
                "description": "Updated description",
                "avatarId": 2,
                "enableRank": True,
                "announcements": ["Announcement 1"],
                "taskTemplates": ["Template 1"],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]["space"]
        assert data["name"] == updated_name
        assert data["intro"] == updated_intro
        assert data["enableRank"] is True

    def test_delete_space(self, setup_space: dict, api_client: TestClient):
        creator = setup_space["creator"]
        space_id = setup_space["space_id"]
        resp = api_client.delete(
            f"/spaces/{space_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"
        resp = api_client.get(
            f"/spaces/{space_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 404


class TestSpaceEnumeration:
    def test_enumerate_spaces(self, user_client: UserCreator, api_client: TestClient):
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)
        for i in range(3):
            suffix = unique_int(10000000, 99999999)
            api_client.post(
                "/spaces",
                json={
                    "name": f"Enum Space ({suffix}) {i}",
                    "intro": "Test",
                    "description": "Desc",
                    "avatarId": 1,
                },
                headers={"Authorization": f"Bearer {creator.token}"},
            )
        resp = api_client.get(
            "/spaces",
            params={"pageSize": 10},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "spaces" in data
        assert "page" in data

    def test_enumerate_spaces_pagination(self, user_client: UserCreator, api_client: TestClient):
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)
        for i in range(5):
            suffix = unique_int(10000000, 99999999)
            api_client.post(
                "/spaces",
                json={
                    "name": f"Page Space ({suffix}) {i}",
                    "intro": "Test",
                    "description": "Desc",
                    "avatarId": 1,
                },
                headers={"Authorization": f"Bearer {creator.token}"},
            )
        resp = api_client.get(
            "/spaces",
            params={"pageSize": 2},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200
        page = resp.json()["data"]["page"]
        assert page["pageSize"] == 2


class TestSpaceCategories:
    @pytest.fixture
    def setup_space_with_category(self, user_client: UserCreator, api_client: TestClient) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)
        suffix = unique_int(10000000, 99999999)
        resp = api_client.post(
            "/spaces",
            json={
                "name": f"Category Space ({suffix})",
                "intro": "Test",
                "description": "Desc",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 201
        space_id = resp.json()["data"]["space"]["id"]
        default_cat_id = resp.json()["data"]["space"].get("defaultCategoryId")
        return {
            "creator": creator,
            "space_id": space_id,
            "default_category_id": default_cat_id,
        }

    def test_create_category(self, setup_space_with_category: dict, api_client: TestClient):
        creator = setup_space_with_category["creator"]
        space_id = setup_space_with_category["space_id"]
        resp = api_client.post(
            f"/spaces/{space_id}/categories",
            json={
                "name": "Backend Tasks",
                "description": "Tasks related to backend development",
                "displayOrder": 10,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 201
        data = resp.json()["data"]["category"]
        assert data["name"] == "Backend Tasks"
        assert data["displayOrder"] == 10

    def test_list_categories(self, setup_space_with_category: dict, api_client: TestClient):
        creator = setup_space_with_category["creator"]
        space_id = setup_space_with_category["space_id"]
        api_client.post(
            f"/spaces/{space_id}/categories",
            json={"name": "Cat1", "description": "Desc", "displayOrder": 1},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        api_client.post(
            f"/spaces/{space_id}/categories",
            json={"name": "Cat2", "description": "Desc", "displayOrder": 2},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        resp = api_client.get(
            f"/spaces/{space_id}/categories",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200
        categories = resp.json()["data"]["categories"]
        assert len(categories) >= 2

    def test_update_category(self, setup_space_with_category: dict, api_client: TestClient):
        creator = setup_space_with_category["creator"]
        space_id = setup_space_with_category["space_id"]
        create_resp = api_client.post(
            f"/spaces/{space_id}/categories",
            json={"name": "Original Name", "description": "Desc", "displayOrder": 1},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 201
        cat_id = create_resp.json()["data"]["category"]["id"]
        resp = api_client.patch(
            f"/spaces/{space_id}/categories/{cat_id}",
            json={"name": "Updated Name", "description": "Updated Desc", "displayOrder": 15},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()["data"]["category"]
        assert data["name"] == "Updated Name"
        assert data["displayOrder"] == 15

    def test_set_default_category(self, setup_space_with_category: dict, api_client: TestClient):
        creator = setup_space_with_category["creator"]
        space_id = setup_space_with_category["space_id"]
        create_resp = api_client.post(
            f"/spaces/{space_id}/categories",
            json={"name": "New Default", "description": "Desc", "displayOrder": 1},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 201
        new_default_id = create_resp.json()["data"]["category"]["id"]
        resp = api_client.patch(
            f"/spaces/{space_id}",
            json={"defaultCategoryId": new_default_id},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert resp.json()["data"]["space"]["defaultCategoryId"] == new_default_id

    def test_archive_category(self, setup_space_with_category: dict, api_client: TestClient):
        creator = setup_space_with_category["creator"]
        space_id = setup_space_with_category["space_id"]
        create_resp = api_client.post(
            f"/spaces/{space_id}/categories",
            json={"name": "To Archive", "description": "Desc", "displayOrder": 1},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 201
        cat_id = create_resp.json()["data"]["category"]["id"]
        resp = api_client.post(
            f"/spaces/{space_id}/categories/{cat_id}/archive",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert resp.json()["data"]["category"]["archivedAt"] is not None

    def test_unarchive_category(self, setup_space_with_category: dict, api_client: TestClient):
        creator = setup_space_with_category["creator"]
        space_id = setup_space_with_category["space_id"]
        create_resp = api_client.post(
            f"/spaces/{space_id}/categories",
            json={"name": "To Archive and Unarchive", "description": "Desc", "displayOrder": 1},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 201
        cat_id = create_resp.json()["data"]["category"]["id"]
        archive_resp = api_client.post(
            f"/spaces/{space_id}/categories/{cat_id}/archive",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert archive_resp.status_code == 200, f"Archive failed: {archive_resp.status_code}"
        resp = api_client.delete(
            f"/spaces/{space_id}/categories/{cat_id}/archive",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert resp.json()["data"]["category"]["archivedAt"] is None

    def test_list_categories_excludes_archived(
        self, setup_space_with_category: dict, api_client: TestClient
    ):
        creator = setup_space_with_category["creator"]
        space_id = setup_space_with_category["space_id"]
        create_resp = api_client.post(
            f"/spaces/{space_id}/categories",
            json={"name": "Will Archive", "description": "Desc", "displayOrder": 1},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        cat_id = create_resp.json()["data"]["category"]["id"]
        archive_resp = api_client.post(
            f"/spaces/{space_id}/categories/{cat_id}/archive",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        if archive_resp.status_code != 200:
            return
        resp = api_client.get(
            f"/spaces/{space_id}/categories",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200
        categories = resp.json()["data"]["categories"]
        cat_ids = [c["id"] for c in categories]
        assert cat_id not in cat_ids

    def test_list_categories_includes_archived_when_requested(
        self, setup_space_with_category: dict, api_client: TestClient
    ):
        creator = setup_space_with_category["creator"]
        space_id = setup_space_with_category["space_id"]
        create_resp = api_client.post(
            f"/spaces/{space_id}/categories",
            json={"name": "Will Archive Include", "description": "Desc", "displayOrder": 1},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        cat_id = create_resp.json()["data"]["category"]["id"]
        api_client.post(
            f"/spaces/{space_id}/categories/{cat_id}/archive",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        resp = api_client.get(
            f"/spaces/{space_id}/categories",
            params={"includeArchived": True},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200
        categories = resp.json()["data"]["categories"]
        cat_ids = [c["id"] for c in categories]
        assert cat_id in cat_ids

    def test_delete_non_default_category(
        self, setup_space_with_category: dict, api_client: TestClient
    ):
        creator = setup_space_with_category["creator"]
        space_id = setup_space_with_category["space_id"]
        create_resp = api_client.post(
            f"/spaces/{space_id}/categories",
            json={"name": "To Delete", "description": "Desc", "displayOrder": 1},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert create_resp.status_code == 201
        cat_id = create_resp.json()["data"]["category"]["id"]
        resp = api_client.delete(
            f"/spaces/{space_id}/categories/{cat_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"


class TestSpacePermissions:
    @pytest.fixture
    def setup_two_users(self, user_client: UserCreator, api_client: TestClient) -> dict:
        owner = user_client.create_user()
        owner.token = user_client.login(api_client, owner.username, owner.password)
        other = user_client.create_user()
        other.token = user_client.login(api_client, other.username, other.password)
        suffix = unique_int(10000000, 99999999)
        resp = api_client.post(
            "/spaces",
            json={
                "name": f"Permission Space ({suffix})",
                "intro": "Test",
                "description": "Desc",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 201
        space_id = resp.json()["data"]["space"]["id"]
        return {
            "owner": owner,
            "other": other,
            "space_id": space_id,
        }

    def test_patch_space_fails_for_non_admin(self, setup_two_users: dict, api_client: TestClient):
        other = setup_two_users["other"]
        space_id = setup_two_users["space_id"]
        resp = api_client.patch(
            f"/spaces/{space_id}",
            json={"name": "Hacked Name"},
            headers={"Authorization": f"Bearer {other.token}"},
        )
        assert resp.status_code == 403, f"Expected 403 Forbidden, got {resp.status_code}"

    def test_delete_space_fails_for_non_admin(self, setup_two_users: dict, api_client: TestClient):
        other = setup_two_users["other"]
        space_id = setup_two_users["space_id"]
        resp = api_client.delete(
            f"/spaces/{space_id}",
            headers={"Authorization": f"Bearer {other.token}"},
        )
        assert resp.status_code == 403, f"Expected 403 Forbidden, got {resp.status_code}"


class TestSpaceAdmins:
    @pytest.fixture
    def setup_space_with_admin(self, user_client: UserCreator, api_client: TestClient) -> dict:
        owner = user_client.create_user()
        owner.token = user_client.login(api_client, owner.username, owner.password)
        admin = user_client.create_user()
        admin.token = user_client.login(api_client, admin.username, admin.password)
        new_owner = user_client.create_user()
        new_owner.token = user_client.login(api_client, new_owner.username, new_owner.password)
        suffix = unique_int(10000000, 99999999)
        resp = api_client.post(
            "/spaces",
            json={
                "name": f"Admin Space ({suffix})",
                "intro": "Test",
                "description": "Desc",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 201
        space_id = resp.json()["data"]["space"]["id"]
        return {
            "owner": owner,
            "admin": admin,
            "new_owner": new_owner,
            "space_id": space_id,
        }

    def test_add_space_admin(self, setup_space_with_admin: dict, api_client: TestClient):
        owner = setup_space_with_admin["owner"]
        admin = setup_space_with_admin["admin"]
        space_id = setup_space_with_admin["space_id"]
        resp = api_client.post(
            f"/spaces/{space_id}/managers",
            json={"userId": admin.user_id, "role": "ADMIN"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 201

    def test_list_space_admins(self, setup_space_with_admin: dict, api_client: TestClient):
        owner = setup_space_with_admin["owner"]
        admin = setup_space_with_admin["admin"]
        space_id = setup_space_with_admin["space_id"]
        api_client.post(
            f"/spaces/{space_id}/managers",
            json={"userId": admin.user_id, "role": "ADMIN"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        resp = api_client.get(
            f"/spaces/{space_id}/managers",
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 200
        managers = resp.json()["data"]["managers"]
        assert len(managers) >= 2

    def test_remove_space_admin(self, setup_space_with_admin: dict, api_client: TestClient):
        owner = setup_space_with_admin["owner"]
        admin = setup_space_with_admin["admin"]
        space_id = setup_space_with_admin["space_id"]
        api_client.post(
            f"/spaces/{space_id}/managers",
            json={"userId": admin.user_id, "role": "ADMIN"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        resp = api_client.delete(
            f"/spaces/{space_id}/managers/{admin.user_id}",
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

    def test_transfer_ownership(self, setup_space_with_admin: dict, api_client: TestClient):
        owner = setup_space_with_admin["owner"]
        new_owner = setup_space_with_admin["new_owner"]
        space_id = setup_space_with_admin["space_id"]
        resp = api_client.post(
            f"/spaces/{space_id}/managers",
            json={"userId": new_owner.user_id, "role": "OWNER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

    def test_add_admin_fails_after_losing_ownership(
        self, setup_space_with_admin: dict, api_client: TestClient
    ):
        owner = setup_space_with_admin["owner"]
        admin = setup_space_with_admin["admin"]
        new_owner = setup_space_with_admin["new_owner"]
        space_id = setup_space_with_admin["space_id"]
        transfer_resp = api_client.post(
            f"/spaces/{space_id}/managers",
            json={"userId": new_owner.user_id, "role": "OWNER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert transfer_resp.status_code == 201, f"Transfer failed: {transfer_resp.status_code}"
        resp = api_client.post(
            f"/spaces/{space_id}/managers",
            json={"userId": admin.user_id, "role": "ADMIN"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 403, (
            f"Expected 403 after losing ownership, got {resp.status_code}"
        )


class TestSpaceDomainGroups:
    """Integration tests for domain group CRUD + access control.

    Verifies:
    - Admin can create/list/update/delete domain groups.
    - Non-admin users CAN list domain groups (needed for task publish/edit).
    - Non-admin users CANNOT create/update/delete domain groups.
    """

    @pytest.fixture
    def setup_space_with_groups(self, user_client: UserCreator, api_client: TestClient) -> dict:
        """Create a space owned by *owner*, then create 2 domain groups as owner."""
        owner = user_client.create_user()
        owner.token = user_client.login(api_client, owner.username, owner.password)
        suffix = unique_int(10000000, 99999999)
        resp = api_client.post(
            "/spaces",
            json={
                "name": f"DG Space ({suffix})",
                "intro": "Domain group test",
                "description": "Desc",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 201
        space_id = resp.json()["data"]["space"]["id"]

        # Create two domain groups
        g1 = api_client.post(
            f"/spaces/{space_id}/domain-groups",
            json={"name": "北京大学", "description": "北大邮箱", "domains": ["pku.edu.cn"]},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert g1.status_code == 201
        g2 = api_client.post(
            f"/spaces/{space_id}/domain-groups",
            json={
                "name": "清华大学",
                "description": None,
                "domains": ["tsinghua.edu.cn", "mail.tsinghua.edu.cn"],
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert g2.status_code == 201

        return {
            "owner": owner,
            "space_id": space_id,
            "group1_id": g1.json()["data"]["group"]["id"],
            "group2_id": g2.json()["data"]["group"]["id"],
        }

    # -- list (read) -----------------------------------------------------------------

    def test_admin_can_list_domain_groups(
        self, setup_space_with_groups: dict, api_client: TestClient
    ):
        owner = setup_space_with_groups["owner"]
        space_id = setup_space_with_groups["space_id"]
        resp = api_client.get(
            f"/spaces/{space_id}/domain-groups",
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 200
        groups = resp.json()["data"]["groups"]
        assert len(groups) == 2
        names = {g["name"] for g in groups}
        assert names == {"北京大学", "清华大学"}

    def test_non_admin_can_list_domain_groups(
        self, setup_space_with_groups: dict, user_client: UserCreator, api_client: TestClient
    ):
        """Regression test: non-admin users must be able to list domain groups
        so they can select access-control domains when publishing/editing tasks."""
        space_id = setup_space_with_groups["space_id"]
        other = user_client.create_user()
        other.token = user_client.login(api_client, other.username, other.password)

        resp = api_client.get(
            f"/spaces/{space_id}/domain-groups",
            headers={"Authorization": f"Bearer {other.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        groups = resp.json()["data"]["groups"]
        assert len(groups) == 2
        # Response should include domain list per group
        for g in groups:
            assert "domains" in g
            assert len(g["domains"]) >= 1

    def test_unauthenticated_cannot_list_domain_groups(
        self, setup_space_with_groups: dict, api_client: TestClient
    ):
        space_id = setup_space_with_groups["space_id"]
        resp = api_client.get(f"/spaces/{space_id}/domain-groups")
        assert resp.status_code == 401

    # -- create ---------------------------------------------------------------------

    def test_admin_can_create_domain_group(
        self, setup_space_with_groups: dict, api_client: TestClient
    ):
        owner = setup_space_with_groups["owner"]
        space_id = setup_space_with_groups["space_id"]
        resp = api_client.post(
            f"/spaces/{space_id}/domain-groups",
            json={"name": "人民大学", "domains": ["ruc.edu.cn"]},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["group"]["name"] == "人民大学"

    def test_non_admin_cannot_create_domain_group(
        self, setup_space_with_groups: dict, user_client: UserCreator, api_client: TestClient
    ):
        space_id = setup_space_with_groups["space_id"]
        other = user_client.create_user()
        other.token = user_client.login(api_client, other.username, other.password)

        resp = api_client.post(
            f"/spaces/{space_id}/domain-groups",
            json={"name": "Hacker", "domains": ["evil.com"]},
            headers={"Authorization": f"Bearer {other.token}"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"

    # -- update ---------------------------------------------------------------------

    def test_admin_can_update_domain_group(
        self, setup_space_with_groups: dict, api_client: TestClient
    ):
        owner = setup_space_with_groups["owner"]
        space_id = setup_space_with_groups["space_id"]
        gid = setup_space_with_groups["group1_id"]
        resp = api_client.patch(
            f"/spaces/{space_id}/domain-groups/{gid}",
            json={"name": "北京大学（已更新）"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["group"]["name"] == "北京大学（已更新）"

    def test_non_admin_cannot_update_domain_group(
        self, setup_space_with_groups: dict, user_client: UserCreator, api_client: TestClient
    ):
        space_id = setup_space_with_groups["space_id"]
        gid = setup_space_with_groups["group1_id"]
        other = user_client.create_user()
        other.token = user_client.login(api_client, other.username, other.password)

        resp = api_client.patch(
            f"/spaces/{space_id}/domain-groups/{gid}",
            json={"name": "Hacked"},
            headers={"Authorization": f"Bearer {other.token}"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"

    # -- delete ---------------------------------------------------------------------

    def test_admin_can_delete_domain_group(
        self, setup_space_with_groups: dict, api_client: TestClient
    ):
        owner = setup_space_with_groups["owner"]
        space_id = setup_space_with_groups["space_id"]
        gid = setup_space_with_groups["group2_id"]
        resp = api_client.delete(
            f"/spaces/{space_id}/domain-groups/{gid}",
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 204

        # Verify it's gone
        list_resp = api_client.get(
            f"/spaces/{space_id}/domain-groups",
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert len(list_resp.json()["data"]["groups"]) == 1

    def test_non_admin_cannot_delete_domain_group(
        self, setup_space_with_groups: dict, user_client: UserCreator, api_client: TestClient
    ):
        space_id = setup_space_with_groups["space_id"]
        gid = setup_space_with_groups["group1_id"]
        other = user_client.create_user()
        other.token = user_client.login(api_client, other.username, other.password)

        resp = api_client.delete(
            f"/spaces/{space_id}/domain-groups/{gid}",
            headers={"Authorization": f"Bearer {other.token}"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"

    # -- validation ----------------------------------------------------------------

    def test_create_domain_group_rejects_invalid_domain(
        self, setup_space_with_groups: dict, api_client: TestClient
    ):
        owner = setup_space_with_groups["owner"]
        space_id = setup_space_with_groups["space_id"]
        resp = api_client.post(
            f"/spaces/{space_id}/domain-groups",
            json={"name": "Bad", "domains": ["not-a-domain-without-dot"]},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 400

    def test_create_domain_group_rejects_empty_name(
        self, setup_space_with_groups: dict, api_client: TestClient
    ):
        owner = setup_space_with_groups["owner"]
        space_id = setup_space_with_groups["space_id"]
        resp = api_client.post(
            f"/spaces/{space_id}/domain-groups",
            json={"name": "   ", "domains": ["example.com"]},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 400

    def test_create_domain_group_rejects_empty_domains(
        self, setup_space_with_groups: dict, api_client: TestClient
    ):
        owner = setup_space_with_groups["owner"]
        space_id = setup_space_with_groups["space_id"]
        resp = api_client.post(
            f"/spaces/{space_id}/domain-groups",
            json={"name": "Empty", "domains": []},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 400
