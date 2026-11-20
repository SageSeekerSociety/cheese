"""Who may do what to a team, asked over HTTP.

Each row below names one thing a person can try on a team, the lowest rank that
may do it, and what everyone below that rank is told. A team ranks its people
owner > admin > member; an outsider is signed in but not on the team, and an
anonymous caller is not signed in at all. A refusal leaves the team as it was;
the allowed request changes it, read back through the API.

Every case starts from the same team: an owner, two admins, two members, one
person waiting on a join request and one holding an invitation.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from tests.conftest import seed_user
from tests.integration.test_team_membership import auth, team_of

OWNER, ADMIN, ADMIN2 = "wm_own", "wm_adm", "wm_adm2"
MEMBER, MEMBER2 = "wm_mem", "wm_mem2"
OUTSIDER, ASKER, INVITEE, NEWBIE = "wm_out", "wm_ask", "wm_inv", "wm_new"
ANON = "anonymous"

Crew = dict[str, Any]


def _headers(crew: Crew, who: str) -> dict[str, str]:
    return {} if who == ANON else auth(crew["tokens"][who])


def _call(client, crew: Crew, who: str, method: str, path: str, body: Any = None):
    kwargs: dict[str, Any] = {"headers": _headers(crew, who)}
    if body is not None:
        kwargs["json"] = body
    return client.request(method, path, **kwargs)


def _ok(resp, status: int = 200) -> dict:
    assert resp.status_code == status, resp.text
    return resp.json()["data"]


def roster(client, crew: Crew) -> dict[str, str]:
    """username -> role, as the owner reads the team."""
    data = _ok(_call(client, crew, OWNER, "GET", f"/teams/{crew['tid']}/members"))
    return {m["user"]["username"]: m["role"] for m in data["members"]}


def pending_requests(client, crew: Crew) -> set[int]:
    data = _ok(
        client.get(
            f"/teams/{crew['tid']}/requests",
            params={"status": "PENDING"},
            headers=_headers(crew, OWNER),
        )
    )
    return {a["id"] for a in data["applications"]}


def pending_invitations(client, crew: Crew) -> set[int]:
    data = _ok(
        client.get(
            f"/teams/{crew['tid']}/invitations",
            params={"status": "PENDING"},
            headers=_headers(crew, OWNER),
        )
    )
    return {a["id"] for a in data["invitations"]}


def invited_to(client, crew: Crew, who: str) -> set[int]:
    data = _ok(
        client.get(
            "/users/me/team-invitations",
            params={"status": "PENDING"},
            headers=_headers(crew, who),
        )
    )
    return {a["teamId"] for a in data["invitations"]}


def join_link_of(client, crew: Crew) -> dict:
    return _ok(_call(client, crew, OWNER, "GET", f"/teams/{crew['tid']}/join-link"))


@pytest.fixture
def crew(client) -> Crew:
    tid = team_of(
        client, owner=OWNER, admins=(ADMIN, ADMIN2), members=(MEMBER, MEMBER2)
    )
    everyone = (OWNER, ADMIN, ADMIN2, MEMBER, MEMBER2, OUTSIDER, ASKER, INVITEE)
    tokens = {h: seed_user(client, h) for h in (*everyone, NEWBIE)}
    out: Crew = {"tid": tid, "tokens": tokens, "ids": {}}
    for handle in tokens:
        found = client.get(
            "/users/lookup", params={"q": handle}, headers=_headers(out, OWNER)
        )
        out["ids"][handle] = _ok(found)["id"]

    asked = _call(client, out, ASKER, "POST", f"/teams/{tid}/join")
    assert _ok(asked)["team"]["joinStatus"] == "pending"
    (out["request"],) = pending_requests(client, out)

    invited = _call(
        client,
        out,
        OWNER,
        "POST",
        f"/teams/{tid}/invitations",
        {"userId": out["ids"][INVITEE], "role": "MEMBER"},
    )
    out["invitation"] = _ok(invited, 201)["invitation"]["id"]
    return out


@dataclass(frozen=True)
class Act:
    """One thing someone may try on the team."""

    method: str
    path: Callable[[Crew], str]
    allowed: tuple[str, int]
    refused: tuple[tuple[str, int], ...]
    body: Callable[[Crew], Any] = lambda crew: None
    # Whether the change has happened, read back through the API; None for reads.
    done: Callable[[Any, Crew], bool] | None = None
    prepare: Callable[[Any, Crew], None] = field(default=lambda client, crew: None)


def _team(crew: Crew) -> str:
    return f"/teams/{crew['tid']}"


def _stealth(client, crew: Crew) -> None:
    _ok(_call(client, crew, OWNER, "PATCH", _team(crew), {"visibility": "stealth"}))


def _remember_link(client, crew: Crew) -> None:
    crew["link"] = join_link_of(client, crew)["token"]


_ADMIN_ONLY = ((MEMBER, 403), (OUTSIDER, 403), (ANON, 401))
_OWNER_ONLY = ((ADMIN, 403), (MEMBER, 403), (OUTSIDER, 403), (ANON, 401))

ACTS: dict[str, Act] = {
    # ── Reading the team ────────────────────────────────────────────────────
    "a stealth team opens to its members; an outsider is told it does not exist": Act(
        "GET",
        _team,
        allowed=(MEMBER, 200),
        refused=((OUTSIDER, 404), (ANON, 401)),
        prepare=_stealth,
    ),
    "a public team opens to anyone signed in, not to an anonymous caller": Act(
        "GET", _team, allowed=(OUTSIDER, 200), refused=((ANON, 401),)
    ),
    "a public team opens by its handle to anyone signed in, not anonymously": Act(
        "GET",
        lambda c: f"/teams/by-handle/t-{OWNER}",
        allowed=(OUTSIDER, 200),
        refused=((ANON, 401),),
    ),
    "a member reads the roster; an outsider cannot": Act(
        "GET",
        lambda c: f"{_team(c)}/members",
        allowed=(MEMBER, 200),
        refused=((OUTSIDER, 403), (ANON, 401)),
    ),
    # ── Changing the team itself ────────────────────────────────────────────
    "an admin renames the team; a member cannot": Act(
        "PATCH",
        _team,
        body=lambda c: {"name": "Renamed by its admin"},
        allowed=(ADMIN, 200),
        refused=_ADMIN_ONLY,
        done=lambda client, c: (
            _ok(_call(client, c, OWNER, "GET", _team(c)))["team"]["name"]
            == "Renamed by its admin"
        ),
    ),
    "only the owner disbands the team; an admin cannot": Act(
        "DELETE",
        _team,
        allowed=(OWNER, 204),
        refused=_OWNER_ONLY,
        done=lambda client, c: (
            _call(client, c, OWNER, "GET", _team(c)).status_code == 404
        ),
    ),
    "only the owner hands the team over; an admin cannot": Act(
        "PUT",
        lambda c: f"{_team(c)}/owner",
        body=lambda c: {"userId": c["ids"][MEMBER2]},
        allowed=(OWNER, 200),
        refused=_OWNER_ONLY,
        done=lambda client, c: roster(client, c)[MEMBER2] == "OWNER",
    ),
    # ── The join link ───────────────────────────────────────────────────────
    "an admin reads the join link; a member cannot": Act(
        "GET",
        lambda c: f"{_team(c)}/join-link",
        allowed=(ADMIN, 200),
        refused=_ADMIN_ONLY,
    ),
    "an admin turns approval off on the join link; a member cannot": Act(
        "PATCH",
        lambda c: f"{_team(c)}/join-link",
        body=lambda c: {"approval": False},
        allowed=(ADMIN, 200),
        refused=_ADMIN_ONLY,
        done=lambda client, c: join_link_of(client, c)["approval"] is False,
    ),
    "an admin resets the join link; a member cannot": Act(
        "POST",
        lambda c: f"{_team(c)}/join-link/reset",
        allowed=(ADMIN, 200),
        refused=_ADMIN_ONLY,
        prepare=_remember_link,
        done=lambda client, c: join_link_of(client, c)["token"] != c["link"],
    ),
    # ── Who is on the roster, and as what ───────────────────────────────────
    "an admin adds someone as a member; a member cannot": Act(
        "POST",
        lambda c: f"{_team(c)}/members",
        body=lambda c: {"userId": c["ids"][NEWBIE], "role": "MEMBER"},
        allowed=(ADMIN, 201),
        refused=_ADMIN_ONLY,
        done=lambda client, c: roster(client, c).get(NEWBIE) == "MEMBER",
    ),
    "only the owner adds someone straight in as an admin; an admin cannot": Act(
        "POST",
        lambda c: f"{_team(c)}/members",
        body=lambda c: {"userId": c["ids"][NEWBIE], "role": "ADMIN"},
        allowed=(OWNER, 201),
        refused=_OWNER_ONLY,
        done=lambda client, c: roster(client, c).get(NEWBIE) == "ADMIN",
    ),
    "only the owner changes a member's role; an admin cannot": Act(
        "PATCH",
        lambda c: f"{_team(c)}/members/{c['ids'][MEMBER2]}",
        body=lambda c: {"role": "ADMIN"},
        allowed=(OWNER, 200),
        refused=_OWNER_ONLY,
        done=lambda client, c: roster(client, c)[MEMBER2] == "ADMIN",
    ),
    "an admin removes a member; a member cannot remove another": Act(
        "DELETE",
        lambda c: f"{_team(c)}/members/{c['ids'][MEMBER2]}",
        allowed=(ADMIN, 204),
        refused=_ADMIN_ONLY,
        done=lambda client, c: MEMBER2 not in roster(client, c),
    ),
    "only the owner removes an admin; another admin cannot": Act(
        "DELETE",
        lambda c: f"{_team(c)}/members/{c['ids'][ADMIN2]}",
        allowed=(OWNER, 204),
        refused=_OWNER_ONLY,
        done=lambda client, c: ADMIN2 not in roster(client, c),
    ),
    # ── Invitations the team sends ──────────────────────────────────────────
    "an admin reads the team's invitations; a member cannot": Act(
        "GET",
        lambda c: f"{_team(c)}/invitations",
        allowed=(ADMIN, 200),
        refused=_ADMIN_ONLY,
    ),
    "an admin invites someone as a member; a member cannot": Act(
        "POST",
        lambda c: f"{_team(c)}/invitations",
        body=lambda c: {"userId": c["ids"][NEWBIE], "role": "MEMBER"},
        allowed=(ADMIN, 201),
        refused=_ADMIN_ONLY,
        done=lambda client, c: c["tid"] in invited_to(client, c, NEWBIE),
    ),
    "only the owner invites someone as an admin; an admin cannot": Act(
        "POST",
        lambda c: f"{_team(c)}/invitations",
        body=lambda c: {"userId": c["ids"][NEWBIE], "role": "ADMIN"},
        allowed=(OWNER, 201),
        refused=_OWNER_ONLY,
        done=lambda client, c: c["tid"] in invited_to(client, c, NEWBIE),
    ),
    "an admin withdraws an invitation; a member cannot": Act(
        "DELETE",
        lambda c: f"{_team(c)}/invitations/{c['invitation']}",
        allowed=(ADMIN, 204),
        refused=_ADMIN_ONLY,
        done=lambda client, c: c["invitation"] not in pending_invitations(client, c),
    ),
    # ── Requests to join ────────────────────────────────────────────────────
    **{
        f"an admin reads the join requests at /{alias}; a member cannot": Act(
            "GET",
            lambda c, alias=alias: f"{_team(c)}/{alias}",
            allowed=(ADMIN, 200),
            refused=_ADMIN_ONLY,
        )
        for alias in ("join-requests", "requests")
    },
    **{
        f"an admin approves a join request at /{alias}; a member cannot": Act(
            "POST",
            lambda c, alias=alias: f"{_team(c)}/{alias}/{c['request']}/approve",
            allowed=(ADMIN, 204),
            refused=_ADMIN_ONLY,
            done=lambda client, c: roster(client, c).get(ASKER) == "MEMBER",
        )
        for alias in ("join-requests", "requests")
    },
    **{
        f"an admin rejects a join request at /{alias}; a member cannot": Act(
            "POST",
            lambda c, alias=alias: f"{_team(c)}/{alias}/{c['request']}/reject",
            allowed=(ADMIN, 204),
            refused=_ADMIN_ONLY,
            done=lambda client, c: (
                c["request"] not in pending_requests(client, c)
                and ASKER not in roster(client, c)
            ),
        )
        for alias in ("join-requests", "requests")
    },
    # ── What any signed-in person does for themself ─────────────────────────
    "anyone signed in creates a team; an anonymous caller cannot": Act(
        "POST",
        lambda c: "/teams",
        body=lambda c: {"name": "A team of my own"},
        allowed=(OUTSIDER, 201),
        refused=((ANON, 401),),
        done=lambda client, c: (
            "A team of my own"
            in {
                t["name"]
                for t in _ok(_call(client, c, OUTSIDER, "GET", "/teams/my-teams"))[
                    "teams"
                ]
            }
        ),
    ),
    "anyone signed in lists their own teams; an anonymous caller cannot": Act(
        "GET",
        lambda c: "/teams/my-teams",
        allowed=(OUTSIDER, 200),
        refused=((ANON, 401),),
    ),
    "anyone signed in asks to join a public team; an anonymous caller cannot": Act(
        "POST",
        lambda c: f"{_team(c)}/join",
        allowed=(NEWBIE, 200),
        refused=((ANON, 401),),
        done=lambda client, c: len(pending_requests(client, c)) == 2,
    ),
    "whoever holds the join link reads the team by it; an anonymous caller cannot": Act(
        "GET",
        lambda c: f"/team-invites/{c['link']}",
        allowed=(OUTSIDER, 200),
        refused=((ANON, 401),),
        prepare=_remember_link,
    ),
    "whoever holds the join link joins by it; an anonymous caller cannot": Act(
        "POST",
        lambda c: f"/team-invites/{c['link']}/join",
        allowed=(NEWBIE, 200),
        refused=((ANON, 401),),
        prepare=_remember_link,
        done=lambda client, c: len(pending_requests(client, c)) == 2,
    ),
    "only the invited person accepts an invitation": Act(
        "POST",
        lambda c: f"/users/me/team-invitations/{c['invitation']}/accept",
        allowed=(INVITEE, 204),
        refused=((OUTSIDER, 404), (OWNER, 404), (ANON, 401)),
        done=lambda client, c: roster(client, c).get(INVITEE) == "MEMBER",
    ),
    "only the invited person declines an invitation": Act(
        "POST",
        lambda c: f"/users/me/team-invitations/{c['invitation']}/decline",
        allowed=(INVITEE, 204),
        refused=((OUTSIDER, 404), (OWNER, 404), (ANON, 401)),
        done=lambda client, c: c["invitation"] not in pending_invitations(client, c),
    ),
    "only the person who asked withdraws a join request": Act(
        "DELETE",
        lambda c: f"/users/me/team-requests/{c['request']}",
        allowed=(ASKER, 204),
        refused=((OUTSIDER, 404), (OWNER, 404), (ANON, 401)),
        done=lambda client, c: c["request"] not in pending_requests(client, c),
    ),
    "a member leaves the team; an anonymous caller cannot": Act(
        "DELETE",
        lambda c: f"/users/me/teams/{c['tid']}",
        allowed=(MEMBER, 204),
        refused=((ANON, 401),),
        done=lambda client, c: MEMBER not in roster(client, c),
    ),
}


@pytest.mark.parametrize("rule", list(ACTS), ids=lambda rule: rule.replace(" ", "_"))
def test_who_may_act_on_a_team(client, crew: Crew, rule: str) -> None:
    act = ACTS[rule]
    act.prepare(client, crew)
    path, body = act.path(crew), act.body(crew)

    for who, status in act.refused:
        resp = _call(client, crew, who, act.method, path, body)
        assert resp.status_code == status, (rule, who, resp.status_code, resp.text)
        if act.done is not None:
            assert not act.done(client, crew), (rule, who, "changed the team")

    who, status = act.allowed
    resp = _call(client, crew, who, act.method, path, body)
    assert resp.status_code == status, (rule, who, resp.status_code, resp.text)
    if act.done is not None:
        assert act.done(client, crew), (rule, who, "left the team unchanged")


def test_an_admin_of_another_team_cannot_answer_this_teams_requests_or_invitations(
    client, crew: Crew
) -> None:
    """Being an admin somewhere does not reach another team's join requests or
    invitations, even through the admin's own team's address."""
    other = team_of(client, owner="wx_own", admins=("wx_adm",))
    token = seed_user(client, "wx_adm")
    for path in (
        f"/teams/{other}/requests/{crew['request']}/approve",
        f"/teams/{other}/requests/{crew['request']}/reject",
    ):
        assert client.post(path, headers=auth(token)).status_code == 404, path
    resp = client.delete(
        f"/teams/{other}/invitations/{crew['invitation']}", headers=auth(token)
    )
    assert resp.status_code == 404, resp.text

    assert crew["request"] in pending_requests(client, crew)
    assert crew["invitation"] in pending_invitations(client, crew)
    assert ASKER not in roster(client, crew)
