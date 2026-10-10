"""The team roster offers 移除成员 exactly where the server lets the reader
remove that member: an admin is never offered another admin."""

import pytest

from tests.conftest import seed_user
from tests.integration.test_team_membership import auth, team_of


def roster(client, team_id: int, reader: str) -> dict[str, bool]:
    r = client.get(f"/teams/{team_id}/members", headers=auth(seed_user(client, reader)))
    assert r.status_code == 200, r.text
    return {m["user"]["username"]: m["canRemove"] for m in r.json()["data"]["members"]}


def user_id(client, team_id: int, reader: str, handle: str) -> int:
    r = client.get(f"/teams/{team_id}/members", headers=auth(seed_user(client, reader)))
    return next(
        m["userId"]
        for m in r.json()["data"]["members"]
        if m["user"]["username"] == handle
    )


def test_an_admin_is_offered_members_but_not_other_admins_or_the_owner(client):
    tid = team_of(client, owner="rr_own", admins=("rr_a1", "rr_a2"), members=("rr_m",))
    assert roster(client, tid, "rr_a1") == {
        "rr_own": False,
        "rr_a1": False,
        "rr_a2": False,
        "rr_m": True,
    }


def test_the_owner_is_offered_everyone_but_themself(client):
    tid = team_of(client, owner="ro_own", admins=("ro_a",), members=("ro_m",))
    assert roster(client, tid, "ro_own") == {
        "ro_own": False,
        "ro_a": True,
        "ro_m": True,
    }


def test_a_plain_member_is_offered_nobody(client):
    tid = team_of(client, owner="rm_own", admins=("rm_a",), members=("rm_m", "rm_n"))
    assert not any(roster(client, tid, "rm_m").values())


@pytest.mark.parametrize(
    ("reader", "target"),
    [
        ("rx_a1", "rx_a2"),
        ("rx_a1", "rx_m"),
        ("rx_own", "rx_a2"),
        ("rx_m", "rx_n"),
    ],
)
def test_what_the_roster_offers_is_what_the_server_accepts(client, reader, target):
    tid = team_of(
        client,
        owner="rx_own",
        admins=("rx_a1", "rx_a2"),
        members=("rx_m", "rx_n"),
    )
    offered = roster(client, tid, reader)[target]
    r = client.delete(
        f"/teams/{tid}/members/{user_id(client, tid, reader, target)}",
        headers=auth(seed_user(client, reader)),
    )
    assert (r.status_code == 204) is offered, r.text
