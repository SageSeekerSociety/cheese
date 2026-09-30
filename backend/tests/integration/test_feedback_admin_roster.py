"""Feedback is administered by its own roster, not by the platform admins.

Private feedback is what its author chose not to show everyone. The platform
admins run the admin screens for other jobs, and being one must not make you a
reader of every private report — nor a triager of the queue.
"""

import pytest

from app.core.config import settings
from tests.integration.conftest import session_auth_headers

TRIAGER = "roster-triager"
PLATFORM_ADMIN = "roster-platform-admin"
REPORTER = "roster-reporter"


@pytest.fixture
def rosters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "feedback_triage_handles", [TRIAGER])
    monkeypatch.setattr(settings, "platform_admin_handles", [PLATFORM_ADMIN])


def _private_report(client) -> dict:
    r = client.post(
        "/feedback",
        json={"title": "只给管理员看", "visibility": "private"},
        headers=session_auth_headers(REPORTER),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_a_platform_admin_is_not_a_feedback_admin(client, rosters):
    row = _private_report(client)
    headers = session_auth_headers(PLATFORM_ADMIN)

    assert client.get("/admin/feedback", headers=headers).status_code == 403
    moved = client.post(
        f"/admin/feedback/{row['id']}/status",
        json={"status": "in_progress"},
        headers=headers,
    )
    assert moved.status_code == 403
    # Someone else's private report does not exist for them.
    assert client.get(f"/feedback/{row['id']}", headers=headers).status_code == 404
    meta = client.get("/feedback/meta", headers=headers).json()["data"]
    assert meta["is_admin"] is False
    assert meta["is_platform_admin"] is True


def test_a_feedback_admin_works_the_queue_and_reads_private_reports(client, rosters):
    row = _private_report(client)
    headers = session_auth_headers(TRIAGER)

    assert client.get("/admin/feedback?tab=private", headers=headers).status_code == 200
    moved = client.post(
        f"/admin/feedback/{row['id']}/status",
        json={"status": "in_progress"},
        headers=headers,
    )
    assert moved.status_code == 200, moved.text
    assert client.get(f"/feedback/{row['id']}", headers=headers).status_code == 200
    meta = client.get("/feedback/meta", headers=headers).json()["data"]
    assert meta["is_admin"] is True
    assert meta["is_platform_admin"] is False


def test_the_platform_screens_follow_the_platform_roster(client, rosters):
    assert (
        client.get(
            "/admin/admins", headers=session_auth_headers(PLATFORM_ADMIN)
        ).status_code
        == 200
    )
    assert (
        client.get("/admin/admins", headers=session_auth_headers(TRIAGER)).status_code
        == 403
    )
