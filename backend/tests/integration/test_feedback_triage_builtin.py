"""Feedback triage ships with the platform, and its absence is a named failure.

Intake is on everywhere; working the queue used to be a rule someone had to
create by hand, and nothing said when nobody had. What is pinned here: the
platform installs exactly one triage rule in the room the deployment names, no
matter how many times it starts; a pause a person set survives a restart and
shows on the admin overview; and on a deployment, an unset setting, a paused
rule or a periodic job that failed each shows on that overview by name, with the
reason, instead of folding into one 「down」 (the settings that are not set at
all, and the periodic jobs: `test_platform_health_names_what_failed.py`).
"""

import uuid

import pytest

from app.core import health
from app.core.config import settings
from app.domain.routine import builtin_triage
from app.domain.topic_membership.services import TopicMemberService
from tests.integration.conftest import post_project, session_auth_headers

OWNER = "fbb-owner"
ADMIN = "fbb-admin"


def _platform_room(client, monkeypatch) -> tuple[str, str]:
    r = post_project(client, json={"name": f"P-{uuid.uuid4().hex[:6]}"}, owner=OWNER)
    assert r.status_code == 200, r.text
    project = r.json()["data"]["id"]
    monkeypatch.setattr(
        settings, "docs_dev_repositories", [f"project-{uuid.UUID(project).hex}/code"]
    )
    r = client.post(
        "/topics",
        json={"project_id": project, "title": "反馈分诊"},
        headers=session_auth_headers(OWNER),
    )
    assert r.status_code == 200, r.text
    return project, r.json()["data"]["id"]


@pytest.fixture(autouse=True)
def _checks_read_this_tests_database(client, monkeypatch):
    """The lifespan binds the check to the app's own session factory; the
    requests of this suite write through `test_request_factory`."""
    monkeypatch.setitem(
        health._domain_checks,
        "feedback_triage",
        lambda: builtin_triage.health(client.test_request_factory),
    )


@pytest.fixture
def admin(monkeypatch) -> str:
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    return ADMIN


@pytest.fixture
def bound(client, monkeypatch) -> tuple[str, str]:
    """A deployment whose settings name a valid room, owner and teammate."""
    project, room = _platform_room(client, monkeypatch)

    async def seats():
        async with client.test_request_factory() as session:
            return await TopicMemberService(session).agent_handles(uuid.UUID(room))

    agent = client.portal.call(seats)[0]
    monkeypatch.setattr(settings, "deployed_via_compose", True)
    monkeypatch.setattr(settings, "feedback_triage_handles", [OWNER])
    monkeypatch.setattr(settings, "feedback_triage_topic_id", room)
    monkeypatch.setattr(settings, "feedback_triage_owner_handle", OWNER)
    monkeypatch.setattr(settings, "feedback_triage_agent_handle", agent)
    return project, room


def _install(client) -> None:
    async def once():
        async with client.test_request_factory() as session:
            await builtin_triage.install(session)
            await session.commit()

    client.portal.call(once)


def _rules(client, project: str, room: str) -> list[dict]:
    r = client.get(
        f"/projects/{project}/routines",
        params={"topic": room},
        headers=session_auth_headers(OWNER),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _check(client, admin: str, name: str) -> dict:
    r = client.get("/admin/stats/platform", headers=session_auth_headers(admin))
    assert r.status_code == 200, r.text
    return r.json()["data"]["health"]["checks"][name]


def test_starting_twice_installs_one_triage_rule(client, bound):
    project, room = bound

    _install(client)
    _install(client)

    rules = _rules(client, project, room)
    assert len(rules) == 1, rules
    assert rules[0]["state"] == "active"
    assert rules[0]["spec"]["feedback_batch"] >= 1


def test_a_pause_survives_a_restart_and_is_named(client, bound, admin):
    project, room = bound
    _install(client)
    rule = _rules(client, project, room)[0]
    r = client.post(
        f"/routines/{rule['id']}/pause", headers=session_auth_headers(OWNER)
    )
    assert r.status_code == 200, r.text

    _install(client)

    assert _rules(client, project, room)[0]["state"] == "paused"
    check = _check(client, admin, "feedback_triage")
    assert check["status"] == "down"
    assert "暂停" in check["detail"]


def test_a_working_rule_reads_up(client, bound, admin):
    _install(client)

    assert _check(client, admin, "feedback_triage")["status"] == "up"


def test_an_owner_off_the_triage_roster_is_named(client, bound, admin, monkeypatch):
    monkeypatch.setattr(settings, "feedback_triage_handles", [])

    check = _check(client, admin, "feedback_triage")

    assert check["status"] == "down"
    assert "FEEDBACK_TRIAGE_OWNER_HANDLE" in check["detail"]
