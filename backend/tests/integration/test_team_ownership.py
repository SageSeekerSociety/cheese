"""团队所有者的两条出路：把团队交给别人（``PUT /teams/{id}/owner``），或解散它。

所有者退不掉团队（后端要他先转让或解散），所以这两条路一条走不通，他就困在自己的
团队里。这里钉的是它们走得通，以及各自拦下的那几种处境：个人团队跟着账号走，交不
出去也解散不了；还有项目的团队不能解散，否则那些项目挂在一个不存在的团队上。
"""

from tests.conftest import seed_user
from tests.integration.test_leaving_a_project_is_not_leaving_the_team import (
    auth,
    project_in,
    team_of,
)


def _members(client, team_id: int, *, who: str) -> dict[str, dict]:
    r = client.get(f"/teams/{team_id}/members", headers=auth(seed_user(client, who)))
    assert r.status_code == 200, r.text
    return {m["user"]["username"]: m for m in r.json()["data"]["members"]}


def _transfer(client, team_id: int, to: int, *, by: str):
    return client.put(
        f"/teams/{team_id}/owner",
        json={"userId": to},
        headers=auth(seed_user(client, by)),
    )


def test_the_owner_hands_the_team_over_and_can_then_leave_it(client):
    tid = team_of(client, owner="cap", members=("mate",))
    mate_id = _members(client, tid, who="cap")["mate"]["user"]["id"]

    r = _transfer(client, tid, mate_id, by="cap")

    assert r.status_code == 200, r.text
    roles = {h: m["role"] for h, m in _members(client, tid, who="mate").items()}
    assert roles == {"mate": "OWNER", "cap": "ADMIN"}
    # 交出去之后他就是普通的一员，走得掉了。
    cap_id = _members(client, tid, who="mate")["cap"]["user"]["id"]
    left = client.delete(
        f"/teams/{tid}/members/{cap_id}", headers=auth(seed_user(client, "cap"))
    )
    assert left.status_code == 204, left.text
    assert "cap" not in _members(client, tid, who="mate")


def test_only_the_owner_can_hand_the_team_over(client):
    tid = team_of(client, owner="cap", members=("mate", "other"))
    other_id = _members(client, tid, who="cap")["other"]["user"]["id"]

    r = _transfer(client, tid, other_id, by="mate")

    assert r.status_code == 403, r.text
    assert _members(client, tid, who="cap")["cap"]["role"] == "OWNER"


def test_the_team_goes_only_to_one_of_its_members(client):
    tid = team_of(client, owner="cap")
    seed_user(client, "stranger")

    r = _transfer(client, tid, _user_id(client, "stranger"), by="cap")

    assert r.status_code == 404, r.text
    assert _members(client, tid, who="cap")["cap"]["role"] == "OWNER"


def test_a_personal_team_cannot_be_handed_over_or_disbanded(client):
    seed_user(client, "solo")
    mine = client.get("/teams/my-teams", headers=auth(seed_user(client, "solo")))
    assert mine.status_code == 200, mine.text
    personal = next(t for t in mine.json()["data"]["teams"] if t.get("personal"))

    handed = _transfer(client, personal["id"], 1, by="solo")
    gone = client.delete(
        f"/teams/{personal['id']}", headers=auth(seed_user(client, "solo"))
    )

    assert handed.status_code == 403, handed.text
    assert gone.status_code == 403, gone.text
    again = client.get("/teams/my-teams", headers=auth(seed_user(client, "solo")))
    assert any(t["id"] == personal["id"] for t in again.json()["data"]["teams"])


def test_a_team_that_still_has_projects_cannot_be_disbanded(client):
    tid = team_of(client, owner="cap")
    project_in(client, tid, owner="cap")

    r = client.delete(f"/teams/{tid}", headers=auth(seed_user(client, "cap")))

    assert r.status_code == 409, r.text
    assert r.json()["error"]["i18n"]["key"] == "teamHasProjects"
    still = client.get(f"/teams/{tid}", headers=auth(seed_user(client, "cap")))
    assert still.status_code == 200, still.text


def test_an_empty_team_is_disbanded(client):
    tid = team_of(client, owner="cap", members=("mate",))

    r = client.delete(f"/teams/{tid}", headers=auth(seed_user(client, "cap")))

    assert r.status_code == 204, r.text
    gone = client.get(f"/teams/{tid}", headers=auth(seed_user(client, "cap")))
    assert gone.status_code == 404, gone.text


def _user_id(client, handle: str) -> int:
    from app.domain.user.repositories import UserRepository

    holder: dict[str, int] = {}

    async def _ask() -> None:
        async with client.test_request_factory() as session:
            user = await UserRepository(session).get_by_username(handle)
            assert user is not None, handle
            holder["id"] = user.id

    client.portal.call(_ask)
    return holder["id"]
