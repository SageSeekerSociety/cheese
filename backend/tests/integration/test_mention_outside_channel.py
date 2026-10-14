"""@-mentioning someone who is not in the channel, as Slack does it.

The sender, and only the sender, is told in the answer to their message whom it
named that the channel does not seat, and whether they may add them. Nobody is
added by being named: the sender chooses, through the channel's own way in.
综合 has everyone in the project, so it has nobody outside it.
"""

from tests.conftest import seed_user
from tests.integration.conftest import (
    in_thread,
    join_project_team,
    post_message,
    post_project,
    session_auth_headers,
)
from tests.integration.test_private_channels import _add, _channel, _project


def _roster(client, tid: str) -> set[str]:
    rows = client.get(
        f"/topics/{tid}/members", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return {row["member_handle"] for row in rows}


def test_naming_someone_outside_a_private_channel_tells_the_sender(client):
    p = _project(client)
    tid = _channel(client, p["id"])
    assert _add(client, tid, "bob", by="alice").status_code == 200

    sent = post_message(client, tid, "alice", {"content": "<@bob> <@carol> 看一下"})

    assert sent["mentioned_outside"] == {
        "room_id": tid,
        "handles": ["carol"],
        "may_add": True,
        "members_only": True,
    }
    # Being named does not bring anyone in.
    assert "carol" not in _roster(client, tid)


def test_nobody_outside_means_nothing_to_tell(client):
    p = _project(client)
    tid = _channel(client, p["id"])
    assert _add(client, tid, "bob", by="alice").status_code == 200

    sent = post_message(client, tid, "alice", {"content": "<@bob> <@all> 看一下"})

    assert "mentioned_outside" not in sent


def test_once_added_through_the_roster_they_are_no_longer_outside(client):
    p = _project(client)
    tid = _channel(client, p["id"])
    first = post_message(client, tid, "alice", {"content": "<@carol> 看一下"})
    assert first["mentioned_outside"]["handles"] == ["carol"]

    assert _add(client, tid, "carol", by="alice").status_code == 200

    again = post_message(client, tid, "alice", {"content": "<@carol> 再看一下"})
    assert "mentioned_outside" not in again


def test_a_public_channel_member_is_told_too(client):
    p = _project(client)
    tid = _channel(client, p["id"], private=False)

    sent = post_message(client, tid, "alice", {"content": "<@bob> 看一下"})

    assert sent["mentioned_outside"]["handles"] == ["bob"]
    assert sent["mentioned_outside"]["may_add"] is True
    assert sent["mentioned_outside"]["members_only"] is False


def test_someone_who_may_not_add_people_is_told_they_may_not(client):
    p = _project(client)
    tid = _channel(client, p["id"], private=False)
    thread = in_thread(client, tid, "alice")

    # bob is not in the channel and does not manage it: he answers in a 支线.
    sent = post_message(client, thread, "bob", {"content": "<@carol> 你怎么看"})

    assert sent["mentioned_outside"]["handles"] == ["carol"]
    assert sent["mentioned_outside"]["room_id"] == tid
    assert sent["mentioned_outside"]["may_add"] is False


def test_whoever_manages_the_project_may_add_from_outside_a_public_channel(client):
    p = _project(client)
    tid = _channel(client, p["id"], private=False)
    thread = in_thread(client, tid, "alice")

    sent = post_message(client, thread, "dave", {"content": "<@carol> 你怎么看"})

    assert sent["mentioned_outside"]["handles"] == ["carol"]
    assert sent["mentioned_outside"]["may_add"] is True


def test_general_has_nobody_outside_it(client):
    p = _project(client)

    sent = post_message(
        client, p["root_topic_id"], "alice", {"content": "<@bob> <@carol> 看一下"}
    )

    assert "mentioned_outside" not in sent


def test_someone_not_in_the_project_is_not_offered(client):
    p = _project(client)
    for who in ("erin", "frank"):
        seed_user(client, who)
    other = post_project(client, json={"name": "Q"}, owner="erin").json()["data"]
    join_project_team(client, other["id"], "frank")
    tid = _channel(client, p["id"])

    sent = post_message(client, tid, "alice", {"content": "<@frank> 看一下"})

    assert "mentioned_outside" not in sent
