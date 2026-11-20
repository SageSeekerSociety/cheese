"""The admin overview's health row names what failed and why.

A check that reads `down` and stops there sends the reader to the logs to find
out which of thirty clocks stopped, or which setting a deployment is missing.
So the overview carries each failing check's reason: the periodic job that
failed and its error, and — on a deployment — the feedback settings nobody
filled in, by their environment names.
"""

import asyncio

import pytest

from app.core import background, health
from app.core.config import settings
from tests.integration.conftest import session_auth_headers

ADMIN = "phn-admin"


@pytest.fixture
def admin(monkeypatch) -> str:
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    return ADMIN


def _check(client, admin: str, name: str) -> dict:
    r = client.get("/admin/stats/platform", headers=session_auth_headers(admin))
    assert r.status_code == 200, r.text
    return r.json()["data"]["health"]["checks"][name]


def test_unset_settings_are_named_on_the_overview(client, admin, monkeypatch):
    monkeypatch.setattr(settings, "deployed_via_compose", True)
    monkeypatch.setattr(settings, "feishu_feedback_webhook", "")
    for name in (
        "feedback_triage_topic_id",
        "feedback_triage_owner_handle",
        "feedback_triage_agent_handle",
    ):
        monkeypatch.setattr(settings, name, "")

    triage = _check(client, admin, "feedback_triage")
    announce = _check(client, admin, "feedback_announce")

    assert triage["status"] == "down"
    for env in (
        "FEEDBACK_TRIAGE_TOPIC_ID",
        "FEEDBACK_TRIAGE_OWNER_HANDLE",
        "FEEDBACK_TRIAGE_AGENT_HANDLE",
    ):
        assert env in triage["detail"]
    assert announce["status"] == "down"
    assert "FEISHU_FEEDBACK_WEBHOOK" in announce["detail"]


class _Leases:
    async def claim(self, name, *, interval_s, who, now) -> bool:
        return True


def test_the_overview_names_the_periodic_job_that_failed(client, admin, monkeypatch):
    async def broken():
        raise LookupError("nothing there")

    job = background.PeriodicRunner("phn broken sweep", 0.01, broken)

    async def fail_once():
        job.start(_Leases())
        for _ in range(200):
            if job.health()["status"] == "failed":
                return
            await asyncio.sleep(0.01)

    client.portal.call(fail_once)
    monkeypatch.setattr(
        health,
        "_periodic_health",
        lambda: background.periodic_health([job], owns_work=True),
    )
    try:
        check = _check(client, admin, "periodic_jobs")
    finally:
        client.portal.call(job.stop)

    assert check["status"] == "down"
    assert "phn broken sweep" in check["detail"]
    assert "LookupError" in check["detail"]
