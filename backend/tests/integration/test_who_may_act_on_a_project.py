"""Who may change what in a project, answered over HTTP, one seat at a time.

A project seats people in this order, from the top (``app.auth.project_roles``):
its **owner**, and the **team owner** who stands above every project of the
team; a **team admin**; someone the project made an **admin**; a **member**,
through the team or (a **guest**) through an invitation from outside it; a
**mentor**, who looks on. Below them sit an **outsider** (signed in, not in the
project), an **AI teammate** working from the project's 综合 with the room
credential its turn carries, and **nobody signed in**.

Each row is one change, named by the permission it exercises
(``app.domain.project_permissions.Permission``), with the answer every seat
gets. Every seat that is refused tries first, and the change is read back as the
owner to show none of them made it; then every seat that may tries in turn and
its change is read back. The answers are pinned exactly as the API gives them
today, including where they look uneven: who is refused 401, 403 or 404 differs
from route to route, and that difference is part of what is pinned.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from tests.conftest import seed_user
from tests.integration.conftest import (
    _register,
    a_team,
    add_external_member,
    join_project_team,
    post_project,
    room_agent_headers,
    session_auth_headers,
)
from tests.integration.test_project_export import exported_project  # noqa: F401
from tests.integration.test_project_reads_need_membership import off_the_street

#: Lowest first. ``agent`` is the project's 芝士 on its room credential.
SEATS = (
    "anonymous",
    "outsider",
    "agent",
    "mentor",
    "member",
    "guest",
    "admin",
    "team_admin",
    "owner",
    "team_owner",
)

#: The person in each seat.
HANDLES = {
    "outsider": "mallory",
    "mentor": "mentor",
    "member": "bob",
    "guest": "erin",
    "admin": "padmin",
    "team_admin": "tadmin",
    "owner": "alice",
    "team_owner": "towner",
}


@dataclass
class Project:
    """A team project with one caller in every seat."""

    client: TestClient
    id: str
    root: str
    team_id: int
    headers: dict[str, dict[str, str]]
    #: Ids of whatever ``prepare`` made for the change under test.
    made: dict[str, str] = field(default_factory=dict)

    @property
    def base(self) -> str:
        return f"/projects/{self.id}"

    def owner_get(self, path: str, **params) -> dict:
        resp = self.client.get(path, params=params, headers=self.headers["owner"])
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]

    def as_owner(self, method: str, path: str, **kwargs) -> Response:
        return self.client.request(
            method, path, headers=self.headers["owner"], **kwargs
        )

    def call(self, seat: str, act: Callable[[Project, dict, str], Response]):
        if seat == "anonymous":
            with off_the_street(self.client):
                return act(self, {}, seat)
        return act(self, self.headers[seat], seat)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _team_of(client: TestClient) -> int:
    """towner's team, with alice, bob and padmin as members and tadmin as an
    admin."""
    from app.domain.team.models import TeamMemberRole, TeamUserRelation
    from app.domain.user.repositories import UserRepository

    for handle in ("alice", "bob", "padmin", "tadmin", "mallory"):
        _register(client, handle)

    async def _seed() -> int:
        now = datetime.now(UTC)
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            team_id = await a_team(session, owner_handle="towner")
            for handle, role in (
                ("alice", TeamMemberRole.MEMBER),
                ("bob", TeamMemberRole.MEMBER),
                ("padmin", TeamMemberRole.MEMBER),
                ("tadmin", TeamMemberRole.ADMIN),
            ):
                user = await UserRepository(session).get_by_username(handle)
                assert user is not None, handle
                session.add(
                    TeamUserRelation(
                        team_id=team_id,
                        user_id=user.id,
                        role=role,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await session.commit()
            return team_id

    return asyncio.run(_seed())


def seat_everyone(client: TestClient) -> Project:
    """alice's project in towner's team, with everyone in their seat."""
    team_id = _team_of(client)
    made = post_project(
        client, {"name": "P", "team_id": team_id}, headers=session_auth_headers("alice")
    )
    assert made.status_code == 200, made.text
    data = made.json()["data"]
    pid = data["id"]
    add_external_member(client, pid, "erin", by="alice")
    add_external_member(client, pid, "mentor", by="alice")
    for handle, role in (("mentor", "mentor"), ("padmin", "admin")):
        set_ = client.put(
            f"/projects/{pid}/members/{handle}/role",
            json={"role": role},
            headers=session_auth_headers("alice"),
        )
        assert set_.status_code == 200, set_.text
    headers = {
        seat: _bearer(seed_user(client, handle)) for seat, handle in HANDLES.items()
    }
    headers["agent"] = room_agent_headers(client, data["root_topic_id"])
    return Project(
        client=client,
        id=pid,
        root=data["root_topic_id"],
        team_id=team_id,
        headers=headers,
    )


@pytest.fixture
def project(client: TestClient) -> Project:
    return seat_everyone(client)


# -- what the owner sets up, and reads back -------------------------------------


def _roster(p: Project) -> dict[str, str | None]:
    rows = p.owner_get(f"{p.base}/members")["data"]
    return {r["user_handle"]: r.get("role") for r in rows if not r["agent"]}


def _invitees(p: Project) -> set[str]:
    return {i["invitee_handle"] for i in p.owner_get(f"{p.base}/invitations")["data"]}


def _register_invitees(p: Project) -> None:
    for seat in SEATS:
        _register(p.client, f"invitee-{seat.replace('_', '-')}")


def _invitee(seat: str) -> str:
    return f"invitee-{seat.replace('_', '-')}"


def _guest_to_remove(p: Project) -> None:
    """A fresh guest each time, so each seat has someone to take out."""
    n = int(p.made.get("removed", "0")) + 1
    p.made["removed"] = str(n)
    p.made["target"] = f"leaving-{n}"
    add_external_member(p.client, p.id, p.made["target"], by="alice")


def _erin_is_a_member_again(p: Project) -> None:
    resp = p.as_owner("PUT", f"{p.base}/members/erin/role", json={"role": "member"})
    assert resp.status_code == 200, resp.text


def _bob_is_a_member_again(p: Project) -> None:
    resp = p.as_owner("PUT", f"{p.base}/members/bob/role", json={"role": "member"})
    assert resp.status_code == 200, resp.text


def _a_second_admin(p: Project) -> None:
    if "second_admin" not in p.made:
        join_project_team(p.client, p.id, "padmin2")
        p.made["second_admin"] = "padmin2"
    resp = p.as_owner("PUT", f"{p.base}/members/padmin2/role", json={"role": "admin"})
    assert resp.status_code == 200, resp.text


def _roles_reset(p: Project) -> None:
    assert p.as_owner("DELETE", f"{p.base}/roles").status_code == 200


def _member_grants(p: Project) -> set[str]:
    return set(p.owner_get(f"{p.base}/roles")["grants"]["member"])


def _naming_auto(p: Project) -> None:
    resp = p.as_owner("PUT", f"{p.base}/task-naming", json={"mode": "auto"})
    assert resp.status_code == 200, resp.text


def _not_strict(p: Project) -> None:
    resp = p.as_owner("PUT", f"{p.base}/branch-protection", json={"strict": False})
    assert resp.status_code == 200, resp.text


def _all_tiers(p: Project) -> None:
    resp = p.as_owner("PUT", f"{p.base}/tier-policy", json={"allowed_tiers": None})
    assert resp.status_code == 200, resp.text


def _a_document(p: Project) -> None:
    resp = p.as_owner("POST", f"{p.base}/documents", json={"title": "共享的文档"})
    assert resp.status_code == 200, resp.text
    p.made["doc"] = resp.json()["data"]["id"]


def _unshared(p: Project) -> None:
    if "doc" not in p.made:
        _a_document(p)
    assert p.as_owner("DELETE", f"/documents/{p.made['doc']}/share").status_code == 200


def _unarchived(p: Project) -> None:
    if p.owner_get(p.base)["archived_at"] is not None:
        assert p.as_owner("POST", f"{p.base}/unarchive").status_code == 200


def _no_offer(p: Project) -> None:
    if p.owner_get(p.base)["pending_transfer"] is not None:
        assert p.as_owner("DELETE", f"{p.base}/transfer").status_code == 200


def _model_follows_the_default(p: Project) -> None:
    resp = p.as_owner("PUT", f"{p.base}/default-model", json={"model": None})
    assert resp.status_code == 200, resp.text


def _the_model(p: Project) -> str:
    """A model the project may choose."""
    return p.owner_get(f"{p.base}/default-model")["choices"][0]["id"]


def _agents(p: Project) -> dict[str, dict]:
    return {a["handle"]: a for a in p.owner_get(f"{p.base}/agents")["data"]}


def _cheese_is_the_default(p: Project) -> None:
    if "designer" not in p.made:
        made = p.as_owner("POST", f"{p.base}/agents", json={"handle": "designer"})
        assert made.status_code == 200, made.text
        p.made["designer"] = made.json()["data"]["id"]
    resp = p.as_owner(
        "PUT",
        f"{p.base}/default-agent",
        json={"instance_id": _agents(p)["cheese"]["id"]},
    )
    assert resp.status_code == 200, resp.text


def _channel_titles(p: Project) -> set[str]:
    return {c["title"] for c in p.owner_get(f"{p.base}/channels")["items"]}


def _document_titles(p: Project) -> set[str]:
    return {d["title"] for d in p.owner_get(f"{p.base}/documents")["data"]}


def _agent_handles(p: Project) -> set[str]:
    return {a["handle"] for a in p.owner_get(f"{p.base}/agents")["data"]}


def _server_names(p: Project) -> set[str]:
    return {s["name"] for s in p.owner_get(f"{p.base}/mcp/servers")["servers"]}


def _template_titles(p: Project) -> set[str]:
    return {t["title"] for t in p.owner_get(f"{p.base}/routine-templates")["data"]}


def _tag(seat: str) -> str:
    return seat.replace("_", "-")


# -- the table ------------------------------------------------------------------


def _answers(**by_seat: int) -> dict[str, int]:
    assert set(by_seat) == set(SEATS), set(SEATS) ^ set(by_seat)
    return by_seat


#: Refused everywhere but the people who manage the project.
_MANAGERS_ONLY = dict(
    anonymous=403,
    outsider=403,
    agent=403,
    mentor=403,
    member=403,
    guest=403,
    admin=200,
    team_admin=200,
    owner=200,
    team_owner=200,
)

#: Settings: a stranger is told 403, nobody signed in 401.
_SETTINGS = {**_MANAGERS_ONLY, "anonymous": 401}

#: What any member may do, and a mentor or the room's AI teammate may not.
_MEMBERS = dict(
    anonymous=401,
    outsider=403,
    agent=403,
    mentor=403,
    member=200,
    guest=200,
    admin=200,
    team_admin=200,
    owner=200,
    team_owner=200,
)

#: Only the owner, and the team owner who stands above every project of the team.
_OWNERS_ONLY = dict(
    outsider=404,
    agent=403,
    mentor=403,
    member=403,
    guest=403,
    admin=403,
    team_admin=403,
    owner=200,
    team_owner=200,
)


@dataclass(frozen=True)
class Change:
    """One change to a project.

    ``act`` is the request a seat sends; ``done`` reads back, as the owner,
    whether that seat's request took effect. ``prepare`` runs before the
    refusals and again before each seat that may, so every seat changes
    something that is there to change.
    """

    rule: str
    permission: str
    act: Callable[[Project, dict, str], Response]
    answers: dict[str, int]
    prepare: Callable[[Project], None] | None = None
    done: Callable[[Project, str], bool] | None = None


CHANGES = [
    # manage_members
    Change(
        "only a manager invites someone into the project",
        "manage_members",
        lambda p, h, s: p.client.post(
            f"{p.base}/invitations", json={"user_handle": _invitee(s)}, headers=h
        ),
        _answers(**_MANAGERS_ONLY),
        prepare=_register_invitees,
        done=lambda p, s: _invitee(s) in _invitees(p),
    ),
    Change(
        "only a manager takes someone out of the project",
        "manage_members",
        lambda p, h, s: p.client.delete(
            f"{p.base}/members/{p.made['target']}", headers=h
        ),
        _answers(**_MANAGERS_ONLY),
        prepare=_guest_to_remove,
        done=lambda p, s: p.made["target"] not in _roster(p),
    ),
    # change_roles
    Change(
        "only a manager makes a member a mentor",
        "change_roles",
        lambda p, h, s: p.client.put(
            f"{p.base}/members/erin/role", json={"role": "mentor"}, headers=h
        ),
        _answers(**_MANAGERS_ONLY),
        prepare=_erin_is_a_member_again,
        done=lambda p, s: _roster(p)["erin"] == "mentor",
    ),
    Change(
        "only an owner makes someone an admin",
        "change_roles",
        lambda p, h, s: p.client.put(
            f"{p.base}/members/bob/role", json={"role": "admin"}, headers=h
        ),
        _answers(**{**_MANAGERS_ONLY, "admin": 403, "team_admin": 403}),
        prepare=_bob_is_a_member_again,
        done=lambda p, s: _roster(p)["bob"] == "admin",
    ),
    Change(
        "an admin does not move another admin; an owner does",
        "change_roles",
        lambda p, h, s: p.client.put(
            f"{p.base}/members/padmin2/role", json={"role": "member"}, headers=h
        ),
        _answers(**{**_MANAGERS_ONLY, "admin": 403, "team_admin": 403}),
        prepare=_a_second_admin,
        done=lambda p, s: _roster(p)["padmin2"] == "member",
    ),
    Change(
        "only a manager changes what a role may do",
        "change_roles",
        lambda p, h, s: p.client.put(
            f"{p.base}/roles",
            json={
                "grants": {
                    "member": sorted(_member_grants(p) - {"create_channels"}),
                }
            },
            headers=h,
        ),
        _answers(**_MANAGERS_ONLY),
        prepare=_roles_reset,
        done=lambda p, s: "create_channels" not in _member_grants(p),
    ),
    # edit_settings
    Change(
        "only a manager renames the project's address",
        "edit_settings",
        lambda p, h, s: p.client.put(
            f"{p.base}/slug", json={"slug": f"addr-{_tag(s)}"}, headers=h
        ),
        _answers(**_SETTINGS),
        done=lambda p, s: p.owner_get(p.base)["slug"] == f"addr-{_tag(s)}",
    ),
    Change(
        "only a manager switches how tasks are named",
        "edit_settings",
        lambda p, h, s: p.client.put(
            f"{p.base}/task-naming", json={"mode": "manual"}, headers=h
        ),
        _answers(**_SETTINGS),
        prepare=_naming_auto,
        done=lambda p, s: p.owner_get(f"{p.base}/task-naming")["mode"] == "manual",
    ),
    Change(
        "only a manager limits which model tiers the project may use",
        "edit_settings",
        lambda p, h, s: p.client.put(
            f"{p.base}/tier-policy",
            json={"allowed_tiers": ["included"], "over_tier": "deny"},
            headers=h,
        ),
        _answers(**_SETTINGS),
        prepare=_all_tiers,
        done=lambda p, s: (
            p.owner_get(f"{p.base}/tier-policy")["allowed_tiers"] == ["included"]
        ),
    ),
    Change(
        "only a manager picks the project's default model",
        "edit_settings",
        lambda p, h, s: p.client.put(
            f"{p.base}/default-model", json={"model": _the_model(p)}, headers=h
        ),
        _answers(**_SETTINGS),
        prepare=_model_follows_the_default,
        done=lambda p, s: (
            p.owner_get(f"{p.base}/default-model")["model"] == _the_model(p)
        ),
    ),
    Change(
        "only a manager changes the merge rules, and the project is hidden "
        "from whoever is not in it",
        "edit_settings",
        lambda p, h, s: p.client.put(
            f"{p.base}/branch-protection", json={"strict": True}, headers=h
        ),
        _answers(**{**_SETTINGS, "anonymous": 404, "outsider": 404}),
        prepare=_not_strict,
        done=lambda p, s: p.owner_get(f"{p.base}/branch-protection")["strict"],
    ),
    # create_channels
    Change(
        "any member opens a channel; a mentor does not",
        "create_channels",
        lambda p, h, s: p.client.post(
            "/topics", json={"project_id": p.id, "title": f"频道-{s}"}, headers=h
        ),
        _answers(**_MEMBERS),
        done=lambda p, s: f"频道-{s}" in _channel_titles(p),
    ),
    # edit_content
    Change(
        "any member writes a document; a mentor does not",
        "edit_content",
        lambda p, h, s: p.client.post(
            f"{p.base}/documents", json={"title": f"文档-{s}"}, headers=h
        ),
        _answers(**_MEMBERS),
        done=lambda p, s: f"文档-{s}" in _document_titles(p),
    ),
    # share_documents
    Change(
        "any member makes a document's read-only link; a mentor does not",
        "share_documents",
        lambda p, h, s: p.client.put(f"/documents/{p.made['doc']}/share", headers=h),
        _answers(**_MEMBERS),
        prepare=_unshared,
        done=lambda p, s: (
            p.owner_get(f"/documents/{p.made['doc']}/share")["link"] is not None
        ),
    ),
    # comment
    Change(
        "a mentor comments on a document like any member",
        "comment",
        lambda p, h, s: p.client.post(
            f"/documents/{p.made['doc']}/comments",
            json={"content": f"意见-{s}"},
            headers=h,
        ),
        _answers(**{**_MEMBERS, "mentor": 200}),
        prepare=lambda p: None if "doc" in p.made else _a_document(p),
        done=lambda p, s: (
            f"意见-{s}"
            in {
                t["comment"]["content"]
                for t in p.owner_get(f"/documents/{p.made['doc']}/comments/threads")[
                    "data"
                ]
            }
        ),
    ),
    # configure_agents
    Change(
        "any member adds an AI teammate; a mentor does not",
        "configure_agents",
        lambda p, h, s: p.client.post(
            f"{p.base}/agents", json={"handle": f"helper-{_tag(s)}"}, headers=h
        ),
        _answers(**_MEMBERS),
        done=lambda p, s: f"helper-{_tag(s)}" in _agent_handles(p),
    ),
    Change(
        "any member picks which AI teammate new channels start with",
        "configure_agents",
        lambda p, h, s: p.client.put(
            f"{p.base}/default-agent",
            json={"instance_id": p.made["designer"]},
            headers=h,
        ),
        _answers(**_MEMBERS),
        prepare=_cheese_is_the_default,
        done=lambda p, s: _agents(p)["designer"]["is_default"],
    ),
    Change(
        "any member adds an MCP server for the project's agents",
        "configure_agents",
        lambda p, h, s: p.client.post(
            f"{p.base}/mcp/servers",
            json={"name": f"lint-{_tag(s)}", "kind": "command", "command": "ruff-mcp"},
            headers=h,
        ),
        _answers(**_MEMBERS),
        done=lambda p, s: f"lint-{_tag(s)}" in _server_names(p),
    ),
    Change(
        "any member saves a routine template",
        "configure_agents",
        lambda p, h, s: p.client.post(
            f"{p.base}/routine-templates",
            json={
                "title": f"周报-{s}",
                "goal": "每周一汇总上周完成的事",
                "trigger": "schedule",
                "spec": {"freq": "weekly", "weekdays": [0], "time": "09:00"},
            },
            headers=h,
        ),
        _answers(**_MEMBERS),
        done=lambda p, s: f"周报-{s}" in _template_titles(p),
    ),
    # what only the owner does
    Change(
        "only an owner archives the project",
        "owner only",
        lambda p, h, s: p.client.post(f"{p.base}/archive", headers=h),
        _answers(**_OWNERS_ONLY, anonymous=401),
        prepare=_unarchived,
        done=lambda p, s: p.owner_get(p.base)["archived_at"] is not None,
    ),
    Change(
        "only an owner offers the project to someone else",
        "owner only",
        lambda p, h, s: p.client.post(
            f"{p.base}/transfer", json={"to_handle": "bob"}, headers=h
        ),
        _answers(**_OWNERS_ONLY, anonymous=404),
        prepare=_no_offer,
        done=lambda p, s: p.owner_get(p.base)["pending_transfer"] is not None,
    ),
    Change(
        # An owner gets past the door to the check that the team exists.
        "only an owner moves the project to another team",
        "owner only",
        lambda p, h, s: p.client.post(
            f"{p.base}/move", json={"team_id": 999_999_999}, headers=h
        ),
        _answers(**{**_OWNERS_ONLY, "owner": 422, "team_owner": 422}, anonymous=404),
    ),
]


@pytest.mark.parametrize("change", CHANGES, ids=lambda c: f"{c.permission}: {c.rule}")
def test_who_may_change_a_project(project: Project, change: Change) -> None:
    """Every seat refused gets its pinned answer and changes nothing; every
    seat that may gets its pinned answer and its change is there to read."""
    if change.prepare is not None:
        change.prepare(project)
    refused = [s for s in SEATS if change.answers[s] >= 400]
    allowed = [s for s in SEATS if change.answers[s] < 400]
    for seat in refused:
        resp = project.call(seat, change.act)
        assert resp.status_code == change.answers[seat], (
            f"{seat}: {resp.status_code} {resp.text}"
        )
        if change.done is not None:
            assert not change.done(project, seat), (
                f"{seat}'s refused request took effect"
            )

    for seat in allowed:
        if change.prepare is not None:
            change.prepare(project)
        resp = project.call(seat, change.act)
        assert resp.status_code == change.answers[seat], (
            f"{seat}: {resp.status_code} {resp.text}"
        )
        if change.done is not None:
            assert change.done(project, seat), f"{seat}'s request did not take effect"


# -- leaving: one's own, and never the owner's ----------------------------------


def test_everyone_but_an_owner_may_leave(project: Project) -> None:
    """Leaving is one's own act: every person in the project may (a mentor,
    an admin, a team admin too) and is then off the roster; an owner is
    refused 403 and stays; a stranger has nothing to leave (409); the room's
    AI teammate and nobody signed in are refused 403."""

    def leave(p: Project, h: dict, s: str) -> Response:
        return p.client.delete(f"{p.base}/membership", headers=h)

    answers = _answers(
        anonymous=403,
        outsider=409,
        agent=403,
        mentor=200,
        member=200,
        guest=200,
        admin=200,
        team_admin=200,
        owner=403,
        team_owner=403,
    )
    for seat in SEATS:
        resp = project.call(seat, leave)
        assert resp.status_code == answers[seat], f"{seat}: {resp.text}"
    roster = _roster(project)
    for seat in ("mentor", "member", "guest", "admin", "team_admin"):
        assert HANDLES[seat] not in roster, seat
    assert {"alice", "towner"} <= roster.keys()


# -- export: everyone who works in the project, as a signed-in person -----------


def test_any_member_exports_the_project_and_a_mentor_does_not(
    client: TestClient,
    exported_project: dict,  # noqa: F811
) -> None:
    """``export_project`` is a member's by default: a member downloads the
    archive; a mentor, a stranger and the room's AI teammate are refused 403,
    nobody signed in 401. (The archive's contents are pinned in
    ``test_project_export.py``.)"""
    pid = str(exported_project["pid"])
    join_project_team(client, pid, "bob")
    add_external_member(client, pid, "mentor", by="export-owner")
    made = client.put(
        f"/projects/{pid}/members/mentor/role",
        json={"role": "mentor"},
        headers=exported_project["headers"],
    )
    assert made.status_code == 200, made.text
    root = client.get(f"/projects/{pid}", headers=exported_project["headers"]).json()[
        "data"
    ]["root_topic_id"]
    path = f"/projects/{pid}/export"

    with off_the_street(client) as anon:
        assert anon.get(path).status_code == 401
    for who, headers in (
        ("outsider", _bearer(seed_user(client, "mallory"))),
        ("agent", room_agent_headers(client, root)),
        ("mentor", _bearer(seed_user(client, "mentor"))),
    ):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 403, f"{who}: {resp.status_code} {resp.text[:300]}"

    resp = client.get(path, headers=_bearer(seed_user(client, "bob")))
    assert resp.status_code == 200, resp.text[:500]
    assert resp.headers["content-type"].startswith("application/x-tar")
