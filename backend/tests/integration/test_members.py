"""Project membership CRUD over HTTP."""

MISSING_PROJECT = "00000000-0000-0000-0000-000000000000"


def _create_project(client, name: str = "Demo") -> str:
    r = client.post("/api/projects", json={"name": name})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def test_add_and_list_members(client):
    project_id = _create_project(client)

    r = client.post(
        f"/api/projects/{project_id}/members",
        json={"user_handle": "alice", "role": "lead"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 200
    assert body["data"]["user_handle"] == "alice"
    assert body["data"]["role"] == "lead"
    assert body["data"]["project_id"] == project_id

    # role defaults to member when omitted
    r = client.post(
        f"/api/projects/{project_id}/members",
        json={"user_handle": "bob"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["role"] == "member"

    r = client.get(f"/api/projects/{project_id}/members")
    body = r.json()
    assert body["data"]["total"] == 2
    handles = [m["user_handle"] for m in body["data"]["data"]]
    assert handles == ["alice", "bob"]


def test_duplicate_member_rejected(client):
    project_id = _create_project(client)
    client.post(
        f"/api/projects/{project_id}/members",
        json={"user_handle": "alice"},
    )
    r = client.post(
        f"/api/projects/{project_id}/members",
        json={"user_handle": "alice"},
    )
    assert r.status_code == 422


def test_update_member_role(client):
    project_id = _create_project(client)
    client.post(
        f"/api/projects/{project_id}/members",
        json={"user_handle": "alice", "role": "member"},
    )

    r = client.put(
        f"/api/projects/{project_id}/members/alice",
        json={"role": "mentor"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["role"] == "mentor"

    r = client.get(f"/api/projects/{project_id}/members")
    assert r.json()["data"]["data"][0]["role"] == "mentor"


def test_update_missing_member_404(client):
    project_id = _create_project(client)
    r = client.put(
        f"/api/projects/{project_id}/members/ghost",
        json={"role": "lead"},
    )
    assert r.status_code == 404


def test_delete_member(client):
    project_id = _create_project(client)
    client.post(
        f"/api/projects/{project_id}/members",
        json={"user_handle": "alice"},
    )

    r = client.delete(f"/api/projects/{project_id}/members/alice")
    assert r.status_code == 200
    assert r.json()["data"]["deleted"] is True

    r = client.get(f"/api/projects/{project_id}/members")
    assert r.json()["data"]["total"] == 0


def test_delete_missing_member_404(client):
    project_id = _create_project(client)
    r = client.delete(f"/api/projects/{project_id}/members/ghost")
    assert r.status_code == 404


def test_endpoints_require_existing_project(client):
    r = client.post(
        f"/api/projects/{MISSING_PROJECT}/members",
        json={"user_handle": "alice"},
    )
    assert r.status_code == 404

    r = client.get(f"/api/projects/{MISSING_PROJECT}/members")
    assert r.status_code == 404

    r = client.put(
        f"/api/projects/{MISSING_PROJECT}/members/alice",
        json={"role": "lead"},
    )
    assert r.status_code == 404

    r = client.delete(f"/api/projects/{MISSING_PROJECT}/members/alice")
    assert r.status_code == 404
