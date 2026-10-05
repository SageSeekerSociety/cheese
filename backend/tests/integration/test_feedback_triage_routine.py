"""A daily routine in a platform room hands its teammate the reports nobody took.

What is pinned is what the teammate receives when the rule fires: the oldest
untouched public reports, as many as the rule asks for, with the triage steps —
and nothing it could not read or that someone already holds. A day with nothing
waiting wakes nobody and tells nobody. A room outside the platform's projects
cannot set the rule up, since its teammate could not claim what it was handed.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.domain.routine.models import Routine
from app.domain.routine.service import sweep
from tests.integration.conftest import post_project, session_auth_headers

DEV = "fbt-dev"
REPORTER = "fbt-reporter"
CLAIMER = "fbt-claimer"


class Runner:
    def __init__(self):
        self.submitted = []

    def submit(self, chat, topic_id, **kwargs):
        self.submitted.append((str(topic_id), kwargs))


def _project(client) -> str:
    r = post_project(client, json={"name": f"P-{uuid.uuid4().hex[:6]}"}, owner=DEV)
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _room(client, project: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project, "title": "反馈分诊"},
        headers=session_auth_headers(DEV),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


@pytest.fixture
def platform_room(client, monkeypatch) -> str:
    """A room in the project whose repository is the platform's own."""
    project = _project(client)
    monkeypatch.setattr(
        settings, "docs_dev_repositories", [f"project-{uuid.UUID(project).hex}/code"]
    )
    return _room(client, project)


def _triage_rule(client, room: str, batch=2):
    return client.post(
        f"/topics/{room}/routines",
        json={
            "title": "每日反馈分诊",
            "instructions": "分诊反馈中心里没人处理的反馈",
            "trigger": "schedule",
            "spec": {"freq": "daily", "time": "09:30", "feedback_batch": batch},
            "timezone": "Asia/Shanghai",
        },
        headers=session_auth_headers(DEV),
    )


def _report(client, title: str, **body) -> dict:
    r = client.post(
        "/feedback",
        json={"title": title, **body},
        headers=session_auth_headers(REPORTER),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _fire(client, routine_id: str) -> Runner:
    async def due():
        async with client.test_request_factory() as session:
            await session.execute(
                update(Routine)
                .where(Routine.id == uuid.UUID(routine_id))
                .values(next_run_at=datetime.now(UTC) - timedelta(minutes=1))
            )
            await session.commit()

    client.portal.call(due)
    runner = Runner()
    client.portal.call(
        lambda: sweep(client.test_request_factory, chat=object(), runner=runner)
    )
    return runner


def _runs(client, routine_id: str) -> list[dict]:
    r = client.get(f"/routines/{routine_id}/runs", headers=session_auth_headers(DEV))
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def test_the_run_carries_the_oldest_reports_nobody_took(client, platform_room):
    oldest = _report(client, "最早的一条")
    second = _report(client, "第二条")
    third = _report(client, "第三条")
    private = _report(client, "私密的一条", visibility="private")
    rule = _triage_rule(client, platform_room)
    assert rule.status_code == 200, rule.text

    runner = _fire(client, rule.json()["data"]["id"])

    assert len(runner.submitted) == 1
    _room_id, kwargs = runner.submitted[0]
    content = kwargs["content"]
    assert oldest["display_id"] in content and "最早的一条" in content
    assert second["display_id"] in content
    assert third["display_id"] not in content, "the batch was bigger than asked"
    assert private["display_id"] not in content
    assert "私密的一条" not in content
    assert "cheese_feedback_claim" in content
    assert "Fixes-feedback" in content


def test_a_report_someone_holds_is_not_handed_out(client, platform_room):
    held = _report(client, "有人在修")
    free = _report(client, "没人管")
    r = client.post(
        f"/feedback/{held['display_id']}/claim", headers=session_auth_headers(DEV)
    )
    assert r.status_code == 200, r.text
    rule = _triage_rule(client, platform_room).json()["data"]

    runner = _fire(client, rule["id"])

    content = runner.submitted[0][1]["content"]
    assert free["display_id"] in content
    assert held["display_id"] not in content


def test_nothing_waiting_wakes_nobody_and_tells_nobody(client, platform_room):
    rule = _triage_rule(client, platform_room).json()["data"]
    project = client.get(
        f"/routines/{rule['id']}", headers=session_auth_headers(DEV)
    ).json()["data"]["project_id"]

    runner = _fire(client, rule["id"])
    _fire(client, rule["id"])

    assert runner.submitted == []
    runs = _runs(client, rule["id"])
    assert runs[0]["status"] == "skipped"
    inbox = client.get(
        f"/projects/{project}/alerts", headers=session_auth_headers(DEV)
    ).json()["data"]["data"]
    assert not [n for n in inbox if "每日反馈分诊" in n["title"]]


def test_a_room_outside_the_platform_cannot_take_triage(client):
    room = _room(client, _project(client))

    r = _triage_rule(client, room)

    assert 400 <= r.status_code < 500, r.text


@pytest.mark.parametrize("batch", [0, 11, "5", True])
def test_the_batch_is_a_small_whole_number(client, platform_room, batch):
    r = _triage_rule(client, platform_room, batch=batch)

    assert 400 <= r.status_code < 500, r.text


def test_an_edit_that_names_the_batch_again_keeps_it(client, platform_room):
    rule = _triage_rule(client, platform_room, batch=3).json()["data"]

    r = client.patch(
        f"/routines/{rule['id']}",
        json={"spec": {"freq": "daily", "time": "10:00", "feedback_batch": 3}},
        headers=session_auth_headers(DEV),
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["spec"]["feedback_batch"] == 3
