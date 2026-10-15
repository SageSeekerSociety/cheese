"""An agent reads only what everyone its answer reaches may read.

Whatever 芝士 reads can end up in its answer, in front of everyone the answer is
shown to. So from a channel it reads that channel, the public channels, and the
private ones every person there is also in; from a DM, what that person may
read. Its seat in a room is not enough on its own.
"""

from app.core.sandbox_auth import mint_scoped_token
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)

REFUSED = (403, 404)


def _project(client) -> dict:
    """dave's project; alice, bob and carol are on its team."""
    for who in ("alice", "bob", "carol", "dave"):
        seed_user(client, who)
    p = post_project(client, json={"name": "P"}, owner="dave").json()["data"]
    for handle in ("alice", "bob", "carol"):
        join_project_team(client, p["id"], handle)
    return p


def _private_channel(client, pid: str, by: str, *others: str) -> str:
    made = client.post(
        "/topics",
        json={"project_id": pid, "title": "机密频道", "members_only": True},
        headers=session_auth_headers(by),
    )
    assert made.status_code == 200, made.text
    tid = made.json()["data"]["id"]
    for handle in others:
        added = client.post(
            f"/topics/{tid}/members",
            json={"handle": handle},
            headers=session_auth_headers(by),
        )
        assert added.status_code == 200, added.text
    return tid


def _dm_with_cheese(client, pid: str, who: str) -> str:
    opened = client.get(
        f"/projects/{pid}/private-chat",
        params={"user_handle": who},
        headers=session_auth_headers(who),
    )
    assert opened.status_code == 200, opened.text
    return opened.json()["data"]["id"]


def _cheese(client, root: str) -> str:
    rows = client.get(
        f"/topics/{root}/members", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return next(row["member_handle"] for row in rows if row["agent"])


def _answering_in(pid: str, room: str, agent: str) -> dict[str, str]:
    return {
        "X-Cheese-Token": mint_scoped_token(
            project_id=pid, topic_id=room, access_scope="project", agent_handle=agent
        )
    }


def _reads(client, room: str, headers: dict[str, str]) -> int:
    return client.get(f"/topics/{room}/blocks", headers=headers).status_code


def test_from_a_public_channel_it_reads_no_private_channel_and_no_dm(client):
    p = _project(client)
    root = p["root_topic_id"]
    cheese = _cheese(client, root)
    secret = _private_channel(client, p["id"], "alice")
    dm = _dm_with_cheese(client, p["id"], "alice")

    token = _answering_in(p["id"], root, cheese)

    assert _reads(client, root, token) == 200
    assert _reads(client, secret, token) in REFUSED
    assert _reads(client, dm, token) in REFUSED


def test_from_a_private_channel_it_reads_what_everyone_there_also_reads(client):
    p = _project(client)
    root = p["root_topic_id"]
    cheese = _cheese(client, root)
    here = _private_channel(client, p["id"], "alice", "bob")
    wider = _private_channel(client, p["id"], "alice", "bob", "carol")
    narrower = _private_channel(client, p["id"], "alice")

    token = _answering_in(p["id"], here, cheese)

    assert _reads(client, here, token) == 200
    assert _reads(client, root, token) == 200
    assert _reads(client, wider, token) == 200
    assert _reads(client, narrower, token) in REFUSED


def test_from_a_dm_it_reads_what_that_person_reads(client):
    p = _project(client)
    root = p["root_topic_id"]
    cheese = _cheese(client, root)
    dm = _dm_with_cheese(client, p["id"], "alice")
    hers = _private_channel(client, p["id"], "alice")
    not_hers = _private_channel(client, p["id"], "bob")

    token = _answering_in(p["id"], dm, cheese)

    assert _reads(client, root, token) == 200
    assert _reads(client, hers, token) == 200
    assert _reads(client, not_hers, token) in REFUSED


def test_a_project_credential_reads_no_dm(client):
    """It answers outside the platform, to whoever holds it: the project's
    people at most, so only what all of them may read."""
    p = _project(client)
    root = p["root_topic_id"]
    dm = _dm_with_cheese(client, p["id"], "alice")
    issued = client.post(
        f"/projects/{p['id']}/agent-credential",
        json={},
        headers=session_auth_headers("dave"),
    )
    assert issued.status_code == 200, issued.text
    token = {"X-Cheese-Token": issued.json()["data"]["token"]}

    assert _reads(client, root, token) == 200
    assert _reads(client, dm, token) in REFUSED
