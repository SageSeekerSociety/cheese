"""知是 team projects (/projects, int-keyed): CRUD + members + tree, per the
reference cheese-backend-nt ProjectController contract."""

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import CreatedUser, UserCreator


class TestTeamProjectsIntegration:
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

        team_resp = self.client.post(
            "/teams",
            json={
                "name": f"Proj Team {uuid.uuid4().hex[:8]}",
                "intro": "t",
                "description": "d",
                "avatarId": 1,
            },
            headers=self.headers,
        )
        assert team_resp.status_code == 201
        self.team_id = team_resp.json()["data"]["team"]["id"]

    def _create_project(self, *, name: str = "P1", parent_id: int | None = None, **kw):
        payload = {
            "name": name,
            "description": "desc",
            "colorCode": "#ff6b35",
            "startDate": 1750000000000,
            "endDate": 1760000000000,
            "teamId": self.team_id,
            "leaderId": self.user.user_id,
        }
        if parent_id is not None:
            payload["parentId"] = parent_id
        payload.update(kw)
        resp = self.client.post("/projects", json=payload, headers=self.headers)
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["project"]

    def test_create_out_of_range_date_is_400_not_500(self):
        # An epoch-ms outside datetime's range (e.g. a bad date-picker value)
        # once crashed _from_ms with an unhandled ValueError -> 500. It must be
        # a clean 400. 10**17 ms -> ~year 3170843, the value seen in prod.
        resp = self.client.post(
            "/projects",
            json={
                "name": "BadDate",
                "description": "d",
                "colorCode": "#ff6b35",
                "startDate": 1750000000000,
                "endDate": 10**17,
                "teamId": self.team_id,
                "leaderId": self.user.user_id,
            },
            headers=self.headers,
        )
        assert resp.status_code == 400, resp.text

    def test_create_returns_reference_shape(self):
        project = self._create_project(name="Shape")
        assert project["name"] == "Shape"
        assert project["colorCode"] == "#ff6b35"
        assert project["startDate"] == 1750000000000
        assert project["team"]["id"] == self.team_id
        assert project["leader"]["id"] == self.user.user_id
        assert project["leader"]["username"] == self.user.username
        assert project["archived"] is False
        assert project["members"]["count"] == 1
        assert project["members"]["examples"][0]["role"] == "LEADER"

    def test_list_builds_one_level_tree(self):
        root = self._create_project(name="Root")
        child = self._create_project(name="Child", parent_id=root["id"])

        resp = self.client.get(
            "/projects", params={"team_id": self.team_id}, headers=self.headers
        )
        assert resp.status_code == 200
        projects = resp.json()["data"]["projects"]
        assert [p["name"] for p in projects] == ["Root"]
        assert [c["id"] for c in projects[0]["children"]] == [child["id"]]

        # parent_id filter returns the children flat
        resp = self.client.get(
            "/projects",
            params={"team_id": self.team_id, "parent_id": root["id"]},
            headers=self.headers,
        )
        assert [p["id"] for p in resp.json()["data"]["projects"]] == [child["id"]]

    def test_get_patch_delete_lifecycle(self):
        project = self._create_project(name="Life")
        pid = project["id"]

        got = self.client.get(f"/projects/{pid}", headers=self.headers)
        assert got.status_code == 200
        assert got.json()["data"]["project"]["name"] == "Life"

        patched = self.client.patch(
            f"/projects/{pid}",
            json={"name": "Life2", "archived": True},
            headers=self.headers,
        )
        assert patched.status_code == 200
        data = patched.json()["data"]["project"]
        assert data["name"] == "Life2" and data["archived"] is True

        deleted = self.client.delete(f"/projects/{pid}", headers=self.headers)
        assert deleted.status_code == 204
        assert (
            self.client.get(f"/projects/{pid}", headers=self.headers).status_code == 404
        )

    def test_member_lifecycle(self):
        project = self._create_project(name="Members")
        pid = project["id"]
        other = self.user_client.create_user()

        added = self.client.post(
            f"/projects/{pid}/members",
            json={"userId": other.user_id, "role": "EXTERNAL"},
            headers=self.headers,
        )
        assert added.status_code == 200
        assert added.json()["data"]["member"]["role"] == "EXTERNAL"

        members = self.client.get(f"/projects/{pid}/members", headers=self.headers)
        roles = {m["user"]["id"]: m["role"] for m in members.json()["data"]["members"]}
        assert roles == {self.user.user_id: "LEADER", other.user_id: "EXTERNAL"}

        # adding a LEADER via the endpoint is forbidden (reference rule)
        as_leader = self.client.post(
            f"/projects/{pid}/members",
            json={"userId": other.user_id, "role": "LEADER"},
            headers=self.headers,
        )
        assert as_leader.status_code == 403

        removed = self.client.delete(
            f"/projects/{pid}/members/{other.user_id}", headers=self.headers
        )
        assert removed.status_code == 204
        members = self.client.get(f"/projects/{pid}/members", headers=self.headers)
        assert len(members.json()["data"]["members"]) == 1

    def test_mutations_require_leader(self):
        project = self._create_project(name="Guarded")
        pid = project["id"]
        outsider = self.user_client.create_user()
        token = self.user_client.login(
            self.client, outsider.username, outsider.password
        )
        outsider_headers = {"Authorization": f"Bearer {token}"}

        assert (
            self.client.patch(
                f"/projects/{pid}", json={"name": "X"}, headers=outsider_headers
            ).status_code
            == 403
        )
        assert (
            self.client.delete(f"/projects/{pid}", headers=outsider_headers).status_code
            == 403
        )
        assert (
            self.client.post(
                f"/projects/{pid}/members",
                json={"userId": outsider.user_id},
                headers=outsider_headers,
            ).status_code
            == 403
        )

    def test_create_requires_team_membership(self):
        outsider = self.user_client.create_user()
        token = self.user_client.login(
            self.client, outsider.username, outsider.password
        )
        outsider_headers = {"Authorization": f"Bearer {token}"}
        resp = self.client.post(
            "/projects",
            json={
                "name": "Nope",
                "description": "d",
                "colorCode": "#000000",
                "startDate": 1,
                "endDate": 2,
                "teamId": self.team_id,
                "leaderId": outsider.user_id,
            },
            headers=outsider_headers,
        )
        assert resp.status_code == 403

    def test_leader_transfer_via_patch(self):
        project = self._create_project(name="Transfer")
        pid = project["id"]
        successor = self.user_client.create_user()

        patched = self.client.patch(
            f"/projects/{pid}",
            json={"leaderId": successor.user_id},
            headers=self.headers,
        )
        assert patched.status_code == 200
        assert patched.json()["data"]["project"]["leader"]["id"] == successor.user_id

        members = self.client.get(f"/projects/{pid}/members", headers=self.headers)
        roles = {m["user"]["id"]: m["role"] for m in members.json()["data"]["members"]}
        assert roles[successor.user_id] == "LEADER"
        assert roles[self.user.user_id] == "MEMBER"
