import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


class TestTeamRecruitment:
    @pytest.fixture
    def recruitment_setup(self, user_client: UserCreator, api_client: TestClient) -> dict:
        """Create a team with an owner, admin, member, and outsider."""
        owner = user_client.create_user()
        owner.token = user_client.login(api_client, owner.username, owner.password)

        admin = user_client.create_user()
        admin.token = user_client.login(api_client, admin.username, admin.password)

        member = user_client.create_user()
        member.token = user_client.login(api_client, member.username, member.password)

        outsider = user_client.create_user()
        outsider.token = user_client.login(api_client, outsider.username, outsider.password)

        suffix = unique_int()
        # Create team
        resp = api_client.post(
            "/teams",
            json={
                "name": f"Recruit Team ({suffix})",
                "intro": "A team for recruitment tests",
                "description": "desc",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 201
        team_id = resp.json()["data"]["team"]["id"]

        # Add admin
        inv_resp = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": admin.user_id, "role": "ADMIN"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert inv_resp.status_code == 201
        inv_id = inv_resp.json()["data"]["invitation"]["id"]
        accept_resp = api_client.post(
            f"/users/me/team-invitations/{inv_id}/accept",
            headers={"Authorization": f"Bearer {admin.token}"},
        )
        assert accept_resp.status_code == 204

        # Add member
        inv_resp2 = api_client.post(
            f"/teams/{team_id}/invitations",
            json={"userId": member.user_id, "role": "MEMBER"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert inv_resp2.status_code == 201
        inv_id2 = inv_resp2.json()["data"]["invitation"]["id"]
        accept_resp2 = api_client.post(
            f"/users/me/team-invitations/{inv_id2}/accept",
            headers={"Authorization": f"Bearer {member.token}"},
        )
        assert accept_resp2.status_code == 204

        return {
            "owner": owner,
            "admin": admin,
            "member": member,
            "outsider": outsider,
            "team_id": team_id,
        }

    # ----- CREATE -----

    def test_owner_can_create_post(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        owner = recruitment_setup["owner"]
        team_id = recruitment_setup["team_id"]

        resp = api_client.post(
            f"/teams/{team_id}/recruitment",
            json={
                "title": "Looking for developers",
                "content": "We need Python developers to join!",
                "contact": "owner@example.com",
                "maxMembers": 5,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["code"] == 201
        post = data["data"]["post"]
        assert post["title"] == "Looking for developers"
        assert post["content"] == "We need Python developers to join!"
        assert post["contact"] == "owner@example.com"
        assert post["maxMembers"] == 5
        assert post["status"] == "OPEN"
        assert post["team"]["id"] == team_id
        assert post["creator"]["id"] == owner.user_id
        assert post["createdAt"] is not None
        assert post["updatedAt"] is not None
        assert post["expiresAt"] is None

    def test_admin_can_create_post(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        admin = recruitment_setup["admin"]
        team_id = recruitment_setup["team_id"]

        resp = api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": "Admin post", "content": "Admin created this"},
            headers={"Authorization": f"Bearer {admin.token}"},
        )
        assert resp.status_code == 201
        post = resp.json()["data"]["post"]
        assert post["creator"]["id"] == admin.user_id

    def test_member_cannot_create_post(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        member = recruitment_setup["member"]
        team_id = recruitment_setup["team_id"]

        resp = api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": "Member post", "content": "Should fail"},
            headers={"Authorization": f"Bearer {member.token}"},
        )
        assert resp.status_code == 403

    def test_outsider_cannot_create_post(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        outsider = recruitment_setup["outsider"]
        team_id = recruitment_setup["team_id"]

        resp = api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": "Outsider post", "content": "Should fail"},
            headers={"Authorization": f"Bearer {outsider.token}"},
        )
        assert resp.status_code == 403

    def test_anonymous_cannot_create_post(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        team_id = recruitment_setup["team_id"]
        resp = api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": "Anon post", "content": "Should fail"},
        )
        assert resp.status_code == 401

    # ----- LIST BY TEAM -----

    def test_list_team_recruitment_posts(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        owner = recruitment_setup["owner"]
        team_id = recruitment_setup["team_id"]

        # Create two posts
        api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": "Post 1", "content": "Content 1"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": "Post 2", "content": "Content 2"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        resp = api_client.get(f"/teams/{team_id}/recruitment")
        assert resp.status_code == 200
        posts = resp.json()["data"]["posts"]
        assert len(posts) >= 2

    # ----- GLOBAL RECRUITMENT PLAZA -----

    def test_browse_recruitment_plaza(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        owner = recruitment_setup["owner"]
        team_id = recruitment_setup["team_id"]

        unique_title = f"UniqueRecruitPost-{unique_int()}"
        api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": unique_title, "content": "Join us!"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        resp = api_client.get("/recruitment")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert "posts" in data["data"]
        assert "page" in data["data"]

    def test_browse_recruitment_with_keyword(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        owner = recruitment_setup["owner"]
        team_id = recruitment_setup["team_id"]

        unique_kw = f"UniqueKW{unique_int()}"
        api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": f"Post with {unique_kw}", "content": "Some content"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        # Search with the unique keyword (short: use ILIKE path)
        resp = api_client.get("/recruitment", params={"keyword": unique_kw})
        assert resp.status_code == 200
        posts = resp.json()["data"]["posts"]
        assert any(unique_kw in p["title"] for p in posts)

    def test_browse_recruitment_pagination(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        owner = recruitment_setup["owner"]
        team_id = recruitment_setup["team_id"]

        for i in range(3):
            api_client.post(
                f"/teams/{team_id}/recruitment",
                json={"title": f"Paginated Post {i}", "content": "c"},
                headers={"Authorization": f"Bearer {owner.token}"},
            )

        resp = api_client.get("/recruitment", params={"pageSize": 2})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["posts"]) <= 2
        page = data["page"]
        assert "hasMore" in page

    # ----- EDIT -----

    def test_creator_can_edit_post(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        owner = recruitment_setup["owner"]
        team_id = recruitment_setup["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": "Original Title", "content": "Original content"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        post_id = create_resp.json()["data"]["post"]["id"]

        patch_resp = api_client.patch(
            f"/recruitment/{post_id}",
            json={"title": "Updated Title", "content": "Updated content", "status": "CLOSED"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert patch_resp.status_code == 200
        post = patch_resp.json()["data"]["post"]
        assert post["title"] == "Updated Title"
        assert post["content"] == "Updated content"
        assert post["status"] == "CLOSED"

    def test_non_creator_cannot_edit_post(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        owner = recruitment_setup["owner"]
        admin = recruitment_setup["admin"]
        team_id = recruitment_setup["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": "Owner post", "content": "Only owner edits"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        post_id = create_resp.json()["data"]["post"]["id"]

        patch_resp = api_client.patch(
            f"/recruitment/{post_id}",
            json={"title": "Admin tries to edit"},
            headers={"Authorization": f"Bearer {admin.token}"},
        )
        assert patch_resp.status_code == 403

    # ----- DELETE -----

    def test_owner_can_delete_post(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        owner = recruitment_setup["owner"]
        team_id = recruitment_setup["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": "To Delete", "content": "Will be deleted"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        post_id = create_resp.json()["data"]["post"]["id"]

        del_resp = api_client.delete(
            f"/recruitment/{post_id}",
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert del_resp.status_code == 204

        # Verify it's gone from global listing
        plaza_resp = api_client.get("/recruitment")
        post_ids = [p["id"] for p in plaza_resp.json()["data"]["posts"]]
        assert post_id not in post_ids

    def test_admin_can_delete_post(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        owner = recruitment_setup["owner"]
        admin = recruitment_setup["admin"]
        team_id = recruitment_setup["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": "Admin will delete", "content": "Content"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        post_id = create_resp.json()["data"]["post"]["id"]

        del_resp = api_client.delete(
            f"/recruitment/{post_id}",
            headers={"Authorization": f"Bearer {admin.token}"},
        )
        assert del_resp.status_code == 204

    def test_member_cannot_delete_post(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        owner = recruitment_setup["owner"]
        member = recruitment_setup["member"]
        team_id = recruitment_setup["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": "Member cannot delete", "content": "Content"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        post_id = create_resp.json()["data"]["post"]["id"]

        del_resp = api_client.delete(
            f"/recruitment/{post_id}",
            headers={"Authorization": f"Bearer {member.token}"},
        )
        assert del_resp.status_code == 403

    def test_outsider_cannot_delete_post(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        owner = recruitment_setup["owner"]
        outsider = recruitment_setup["outsider"]
        team_id = recruitment_setup["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": "Outsider cannot delete", "content": "Content"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        post_id = create_resp.json()["data"]["post"]["id"]

        del_resp = api_client.delete(
            f"/recruitment/{post_id}",
            headers={"Authorization": f"Bearer {outsider.token}"},
        )
        assert del_resp.status_code == 403

    # ----- VALIDATION -----

    def test_create_post_missing_title(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        owner = recruitment_setup["owner"]
        team_id = recruitment_setup["team_id"]

        resp = api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"content": "No title"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 400

    def test_create_post_missing_content(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        owner = recruitment_setup["owner"]
        team_id = recruitment_setup["team_id"]

        resp = api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": "No content"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 400

    def test_delete_nonexistent_post(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        owner = recruitment_setup["owner"]
        resp = api_client.delete(
            "/recruitment/999999999",
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 404

    def test_edit_nonexistent_post(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        owner = recruitment_setup["owner"]
        resp = api_client.patch(
            "/recruitment/999999999",
            json={"title": "Nope"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert resp.status_code == 404

    # ----- RESPONSE SHAPE -----

    def test_response_shape_has_team_and_creator(
        self, api_client: TestClient, recruitment_setup: dict
    ) -> None:
        owner = recruitment_setup["owner"]
        team_id = recruitment_setup["team_id"]

        create_resp = api_client.post(
            f"/teams/{team_id}/recruitment",
            json={"title": "Shape test", "content": "Checking response shape"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        post = create_resp.json()["data"]["post"]

        # Verify team object
        assert "id" in post["team"]
        assert "name" in post["team"]
        assert "intro" in post["team"]
        assert "avatarId" in post["team"]

        # Verify creator object
        assert "id" in post["creator"]
        assert "nickname" in post["creator"]
        assert "avatarId" in post["creator"]
        assert "intro" in post["creator"]
