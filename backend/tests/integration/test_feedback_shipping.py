"""The dev deploy moves the feedback a release fixes to 已上线.

What is pinned here is what the person who filed the report sees once the
release that fixes it is live: the status, a timeline step that names the PR,
and an unread count that moved — the same things a person pressing the admin
button produces.
"""

import pytest

import scripts.ship_feedback as ship_feedback
from tests.integration.conftest import session_auth_headers

REPO = "https://github.com/example/repo"
REPORTER = "ship-reporter"
BYSTANDER = "ship-bystander"


@pytest.fixture
def run_deploy(client, monkeypatch):
    """Run the deploy's script against the database the client reads."""
    monkeypatch.setattr(
        ship_feedback, "async_session_factory", client.test_request_factory
    )

    def run(*messages: str) -> None:
        commits = [
            {"sha": f"{index:040x}", "message": message}
            for index, message in enumerate(messages, start=1)
        ]
        client.portal.call(ship_feedback.main, REPO, commits)

    return run


def _report(client, handle: str, title: str = "按钮点了没反应") -> dict:
    r = client.post(
        "/feedback", json={"title": title}, headers=session_auth_headers(handle)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _detail(client, row: dict, handle: str = REPORTER) -> dict:
    r = client.get(f"/feedback/{row['id']}", headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _unread(client, handle: str) -> int:
    r = client.get("/feedback/counts", headers=session_auth_headers(handle))
    return r.json()["data"]["unread"]


def _number(row: dict) -> int:
    return int(row["display_id"].removeprefix("FB-"))


def test_a_shipped_fix_moves_the_report_to_deployed_and_names_the_pr(
    client, run_deploy
):
    row = _report(client, REPORTER)
    client.post("/feedback/read", headers=session_auth_headers(REPORTER))
    before = _unread(client, REPORTER)

    run_deploy(
        "fix(ui): the button answers (#4321)\n\n"
        "* fix(ui): the button answers\n\n"
        f"Fixes-feedback: FB-{_number(row)}\n"
    )

    detail = _detail(client, row)
    assert detail["status"] == "deployed"
    step = detail["timeline"][-1]
    assert step["status"] == "deployed"
    assert step["by_handle"] is None
    assert "PR #4321" in step["note"]
    assert f"{REPO}/pull/4321" in step["note"]
    assert _unread(client, REPORTER) > before


def test_one_commit_can_fix_several_reports_and_others_are_untouched(
    client, run_deploy
):
    first = _report(client, REPORTER, "一")
    second = _report(client, REPORTER, "二")
    untouched = _report(client, REPORTER, "三")

    run_deploy(
        "fix: two at once (#7)\n\n"
        f"fixes-feedback: fb-{_number(first)}, FB-{_number(second)}\n"
    )

    assert _detail(client, first)["status"] == "deployed"
    assert _detail(client, second)["status"] == "deployed"
    assert _detail(client, untouched)["status"] == "received"


def test_a_number_that_is_not_on_the_fixes_feedback_line_moves_nothing(
    client, run_deploy
):
    row = _report(client, REPORTER)
    number = _number(row)

    # `#N` is a GitHub issue or PR, and a mention outside the line is prose.
    run_deploy(
        f"fix: something (#{number})\n\nFixes #{number}\nSee FB-{number} for context.\n"
    )

    assert _detail(client, row)["status"] == "received"


def test_an_indented_example_of_the_line_moves_nothing(client, run_deploy):
    row = _report(client, REPORTER)

    # A commit that documents the syntax shows it indented, as a quotation.
    run_deploy(
        "docs: explain the convention (#40)\n\n"
        "A fixing commit carries:\n\n"
        f"    Fixes-feedback: FB-{_number(row)}\n"
    )

    assert _detail(client, row)["status"] == "received"


def test_a_second_deploy_does_not_add_a_second_step(client, run_deploy):
    row = _report(client, REPORTER)
    message = f"fix: it (#11)\n\nFixes-feedback: FB-{_number(row)}\n"

    run_deploy(message)
    run_deploy(message)

    steps = [s for s in _detail(client, row)["timeline"] if s["status"] == "deployed"]
    assert len(steps) == 1


def test_a_reopened_report_moves_again_when_the_next_fix_ships(
    client, run_deploy, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "feedback_triage_handles", [BYSTANDER])
    row = _report(client, REPORTER)
    run_deploy(f"fix: first try (#20)\n\nFixes-feedback: FB-{_number(row)}\n")
    r = client.post(
        f"/admin/feedback/{row['id']}/status",
        json={"status": "in_progress"},
        headers=session_auth_headers(BYSTANDER),
    )
    assert r.status_code == 200, r.text

    run_deploy(f"fix: second try (#21)\n\nFixes-feedback: FB-{_number(row)}\n")

    detail = _detail(client, row)
    assert detail["status"] == "deployed"
    assert "PR #21" in detail["timeline"][-1]["note"]


def test_a_commit_without_a_pr_number_links_the_commit(client, run_deploy):
    row = _report(client, REPORTER)

    run_deploy(f"fix: pushed directly\n\nFixes-feedback: FB-{_number(row)}\n")

    note = _detail(client, row)["timeline"][-1]["note"]
    assert f"{REPO}/commit/{1:040x}" in note


def test_a_report_that_does_not_exist_is_skipped_not_fatal(client, run_deploy):
    row = _report(client, REPORTER)

    run_deploy(f"fix: two (#30)\n\nFixes-feedback: FB-999999, FB-{_number(row)}\n")

    assert _detail(client, row)["status"] == "deployed"


def test_a_step_a_person_took_has_no_note(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "feedback_triage_handles", [BYSTANDER])
    row = _report(client, REPORTER)
    client.post(
        f"/admin/feedback/{row['id']}/status",
        json={"status": "resolved"},
        headers=session_auth_headers(BYSTANDER),
    )

    assert all(step["note"] is None for step in _detail(client, row)["timeline"])
