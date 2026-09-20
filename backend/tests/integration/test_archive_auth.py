"""Archival uses the authenticated room manager, never the submitted author."""

from tests.integration.conftest import session_auth_headers


def test_archive_requires_manager_and_records_actual_actor(client):
    project = client.post(
        "/projects", json={"name": "P", "owner_handle": "owner"}
    ).json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "R", "created_by": "owner"},
    ).json()["data"]
    topic = room["id"]
    for handle, role in (("admin", "admin"), ("member", "member")):
        result = client.post(
            f"/topics/{topic}/members",
            json={"handle": handle, "role": role},
            headers=session_auth_headers("owner"),
        )
        assert result.status_code == 200, result.text
    for headers in (
        {},
        session_auth_headers("member"),
        session_auth_headers("stranger"),
    ):
        result = client.post(
            f"/topics/{topic}/archive", json={"by": "owner"}, headers=headers
        )
        assert result.status_code in {401, 403}
    result = client.post(
        f"/topics/{topic}/archive",
        json={"by": "forged"},
        headers=session_auth_headers("admin"),
    )
    assert result.status_code == 200, result.text
    assert result.json()["data"]["cleanup_due_at"] is not None

    # 房间归档是项目的事，落项目总览（结论 14）：房间关掉之后没人再打开它的
    # 时间线，而「少了一个房间」正是项目总览上要读到的一行。
    def archive_note(room):
        blocks = client.get(
            f"/topics/{room}/blocks", headers=session_auth_headers("owner")
        ).json()["data"]["data"]
        return [b for b in blocks if "归档了房间" in (b.get("content") or "")]

    assert archive_note(topic) == []
    (archive,) = archive_note(project["root_topic_id"])
    assert "R" in archive["content"]
    assert archive["author"] == "admin"
    assert "forged" not in archive["content"]
    assert (
        client.get(
            f"/topics/{topic}/cleanup", headers=session_auth_headers("owner")
        ).json()["data"]["state"]
        == "pending"
    )
    assert (
        client.post(
            f"/topics/{topic}/unarchive",
            json={},
            headers=session_auth_headers("member"),
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/topics/{topic}/unarchive", json={}, headers=session_auth_headers("owner")
        ).status_code
        == 200
    )


def test_scoped_agent_cannot_trigger_global_cleanup(client):
    from app.core.sandbox_auth import mint_scoped_token

    token = mint_scoped_token(project_id="project", topic_id="room")
    assert (
        client.post(
            "/sandbox/storage-sweep", headers={"X-Cheese-Token": token}
        ).status_code
        == 401
    )
