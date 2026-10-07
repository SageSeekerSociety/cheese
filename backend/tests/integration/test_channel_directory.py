"""A project's channels from the project's side.

The rules, as stated before the code was written:

- everyone in the project finds every public channel, archived ones too, with
  whether they are in it, how many are in it and its open tasks; a private
  channel only its people find;
- whoever manages the project also finds the private channels they are not
  in, by name, manager and size, and nothing that was said or done in them;
- that manager archives such a channel or names its manager without joining,
  and reads it only after joining, which the channel is told;
- a channel's manager hands it to someone else in the project, a person;
- an external member does not open channels.
"""

from tests.integration.conftest import (
    add_external_member,
    join_project_team,
    post_message,
    post_project,
    session_auth_headers,
)


def _project(client) -> dict:
    """dave's project; alice, bob and carol are on its team."""
    p = post_project(client, json={"name": "P"}, owner="dave").json()["data"]
    for handle in ("alice", "bob", "carol"):
        join_project_team(client, p["id"], handle)
    return p


def _channel(client, pid: str, title: str, *, by: str = "alice", private=False):
    r = client.post(
        "/topics",
        json={"project_id": pid, "title": title, "members_only": private},
        headers=session_auth_headers(by),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _directory(client, pid: str, who: str) -> dict:
    r = client.get(f"/projects/{pid}/channels", headers=session_auth_headers(who))
    assert r.status_code == 200, r.text
    return {c["id"]: c for c in r.json()["data"]["items"]}


def _blocks(client, tid: str, who: str):
    return client.get(f"/topics/{tid}/blocks", headers=session_auth_headers(who))


def test_everyone_finds_the_public_channels_and_only_their_private_ones(client):
    p = _project(client)
    front = _channel(client, p["id"], "前端")
    old = _channel(client, p["id"], "旧频道")
    secret = _channel(client, p["id"], "机密", private=True)
    task = client.post(
        f"/topics/{front}/tasks",
        json={"title": "做个东西"},
        headers=session_auth_headers("alice"),
    )
    assert task.status_code == 200, task.text
    assert (
        client.post(f"/topics/{old}/archive", headers=session_auth_headers("alice"))
    ).status_code == 200

    seen = _directory(client, p["id"], "bob")
    assert front in seen and old in seen and secret not in seen
    assert seen[p["root_topic_id"]]["joined"] is True
    assert seen[front]["joined"] is False
    assert seen[front]["open_tasks"] == 1
    assert seen[old]["archived"] is True
    assert _directory(client, p["id"], "alice")[front]["joined"] is True
    assert secret in _directory(client, p["id"], "alice")


def test_the_project_manager_finds_a_private_channel_but_not_what_is_in_it(client):
    p = _project(client)
    secret = _channel(client, p["id"], "机密", private=True)
    client.put(
        f"/topics/{secret}/description",
        json={"description": "只给在场的人"},
        headers=session_auth_headers("alice"),
    )
    post_message(client, secret, "alice", {"content": "里面说的话"})

    row = _directory(client, p["id"], "dave")[secret]
    assert row["title"] == "机密"
    assert row["manager"] == "alice"
    assert row["member_count"] >= 1
    assert row["description"] is None and row["open_tasks"] is None
    assert _blocks(client, secret, "dave").status_code == 404


def test_the_project_manager_archives_a_private_channel_from_outside(client):
    p = _project(client)
    secret = _channel(client, p["id"], "机密", private=True)

    # Someone else outside it is not told it exists.
    outside = client.post(
        f"/topics/{secret}/archive", headers=session_auth_headers("bob")
    )
    assert outside.status_code == 404, outside.text
    archived = client.post(
        f"/topics/{secret}/archive", headers=session_auth_headers("dave")
    )
    assert archived.status_code == 200, archived.text
    assert _directory(client, p["id"], "alice")[secret]["archived"] is True
    assert _blocks(client, secret, "dave").status_code == 404


def test_the_project_manager_reads_it_only_after_joining_and_its_people_are_told(
    client,
):
    p = _project(client)
    secret = _channel(client, p["id"], "机密", private=True)

    refused = client.post(
        f"/projects/{p['id']}/channels/{secret}/step-in",
        headers=session_auth_headers("bob"),
    )
    assert refused.status_code == 404, refused.text
    joined = client.post(
        f"/projects/{p['id']}/channels/{secret}/step-in",
        headers=session_auth_headers("dave"),
    )
    assert joined.status_code == 200, joined.text

    assert _blocks(client, secret, "dave").status_code == 200
    said = _blocks(client, secret, "alice").json()["data"]["data"]
    assert any("dave" in (b.get("content") or "") for b in said), "频道里的人没被告知"


def test_a_channel_is_handed_to_another_person(client):
    p = _project(client)
    tid = _channel(client, p["id"], "前端")

    def hand(to, by):
        return client.put(
            f"/projects/{p['id']}/channels/{tid}/manager",
            json={"handle": to},
            headers=session_auth_headers(by),
        )

    assert hand("bob", "carol").status_code == 403
    assert hand("bob", "alice").status_code == 200
    row = _directory(client, p["id"], "bob")[tid]
    assert row["manager"] == "bob" and row["can_manage"] is True
    assert _directory(client, p["id"], "alice")[tid]["can_manage"] is False
    assert hand("nobody-here", "bob").status_code == 422
    assert hand("bob", "dave").status_code == 200


def test_an_external_member_does_not_open_channels(client):
    p = _project(client)
    add_external_member(client, p["id"], "erin", by="dave")
    r = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "外部的频道"},
        headers=session_auth_headers("erin"),
    )
    assert r.status_code == 403, r.text
    assert _channel(client, p["id"], "队内的频道", by="bob")


def test_someone_outside_a_private_chat_cannot_archive_it(client):
    p = _project(client)
    r = client.get(
        f"/projects/{p['id']}/private-chat",
        params={"user_handle": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    dm = r.json()["data"]["id"]

    outside = client.post(f"/topics/{dm}/archive", headers=session_auth_headers("dave"))
    assert outside.status_code == 404, outside.text
