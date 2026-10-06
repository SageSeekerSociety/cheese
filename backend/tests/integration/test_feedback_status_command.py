"""Moving a report from a shell records the command, not a person.

What the submitter sees after an operator runs `scripts.feedback_status` is what
is pinned here: the status, a timeline step that nobody pressed (`by_handle`
null) whose note says why and who ran it, and an unread count that moved — and
no step at all when the status was already the one asked for.
"""

import pytest

import scripts.feedback_status as feedback_status
from app.domain.feedback.models import FeedbackStatus
from tests.integration.conftest import session_auth_headers

REPORTER = "status-cmd-reporter"


@pytest.fixture
def run_command(client, monkeypatch):
    monkeypatch.setattr(
        feedback_status, "async_session_factory", client.test_request_factory
    )

    def run(ref: str, status: str, *, note: str = "设计如此", by: str = "运维") -> int:
        return client.portal.call(
            lambda: feedback_status.main(ref, FeedbackStatus(status), note=note, by=by)
        )

    return run


def _report(client) -> dict:
    r = client.post(
        "/feedback",
        json={"title": "按钮点了没反应"},
        headers=session_auth_headers(REPORTER),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _detail(client, row: dict) -> dict:
    r = client.get(f"/feedback/{row['id']}", headers=session_auth_headers(REPORTER))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _unread(client) -> int:
    r = client.get("/feedback/counts", headers=session_auth_headers(REPORTER))
    return r.json()["data"]["unread"]


def test_the_step_names_no_account_and_says_why_and_who(client, run_command):
    row = _report(client)
    client.post("/feedback/read", headers=session_auth_headers(REPORTER))
    before = _unread(client)

    code = run_command(
        row["display_id"], "declined", note="这是有意的设计", by="andy 的运维 agent"
    )

    assert code == 0
    detail = _detail(client, row)
    assert detail["status"] == "declined"
    step = detail["timeline"][-1]
    assert step["status"] == "declined"
    assert step["by_handle"] is None
    assert "这是有意的设计" in step["note"]
    assert "andy 的运维 agent" in step["note"]
    assert _unread(client) > before


def test_the_uuid_works_as_well_as_the_number(client, run_command):
    row = _report(client)

    assert run_command(row["id"], "resolved") == 0

    assert _detail(client, row)["status"] == "resolved"


def test_the_status_it_already_has_adds_no_step(client, run_command):
    row = _report(client)
    run_command(row["display_id"], "declined")
    steps = len(_detail(client, row)["timeline"])

    assert run_command(row["display_id"], "declined") == 0

    assert len(_detail(client, row)["timeline"]) == steps


def test_a_report_that_does_not_exist_fails_and_moves_nothing(client, run_command):
    row = _report(client)

    assert run_command("FB-999999999", "declined") == 1

    assert _detail(client, row)["status"] == "received"
