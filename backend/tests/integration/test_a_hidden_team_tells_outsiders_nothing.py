"""A stealth team does not exist for anyone outside it: whatever an outsider
asks of it — its join link, or taking someone off it — is answered exactly as
for a team that is not there, so probing user ids cannot reveal who is on it."""

from tests.conftest import seed_user
from tests.integration.test_team_membership import auth, team_of


def _hidden_team(client) -> tuple[int, dict[str, str], dict[str, int]]:
    tid = team_of(client, owner="ht_own", members=("ht_mem",))
    owner = auth(seed_user(client, "ht_own"))
    hidden = client.patch(
        f"/teams/{tid}", json={"visibility": "stealth"}, headers=owner
    )
    assert hidden.status_code == 200, hidden.text
    roster = client.get(f"/teams/{tid}/members", headers=owner).json()["data"]
    ids = {m["user"]["username"]: m["user"]["id"] for m in roster["members"]}
    return tid, owner, ids


def test_an_outsider_cannot_tell_a_member_from_a_stranger_by_removing_them(client):
    tid, owner, ids = _hidden_team(client)
    stranger_id = ids["ht_own"] + 10_000
    outsider = auth(seed_user(client, "ht_out"))

    on_team = client.delete(f"/teams/{tid}/members/{ids['ht_mem']}", headers=outsider)
    not_on_team = client.delete(f"/teams/{tid}/members/{stranger_id}", headers=outsider)

    assert on_team.status_code == not_on_team.status_code == 404
    roster = client.get(f"/teams/{tid}/members", headers=owner).json()["data"]
    assert {m["user"]["username"] for m in roster["members"]} == {"ht_own", "ht_mem"}


def test_an_outsider_asking_for_a_hidden_teams_join_link_finds_no_team(client):
    tid, _, _ = _hidden_team(client)
    outsider = auth(seed_user(client, "ht_out2"))

    assert client.get(f"/teams/{tid}/join-link", headers=outsider).status_code == 404
    assert client.get("/teams/999999/join-link", headers=outsider).status_code == 404
