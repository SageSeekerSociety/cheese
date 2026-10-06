"""Which executors are told to reach the site on their machine's forward.

A private-control cloud machine reaches the backend on its loopback forward,
and the same SSH session forwards the site over TLS; its executor is given the
site's name and that port. A machine that dials the public base has no such
forward and is given nothing, so its sessions resolve the site as before.
"""

from app.core.config import settings
from app.domain.agent.machine_address import CLOUD_LOOPBACK_BASE
from app.domain.machine import session_work


def _env(api: str) -> dict:
    return session_work._executor_env(
        {},
        api=api,
        token="t",
        project_id="p",
        topic_id="t",
        author="cheese",
        work_resource="r",
    )


def test_a_cloud_machine_on_its_loopback_forward_reaches_the_site_there(monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "https://okcheese.com")

    assert _env(CLOUD_LOOPBACK_BASE)["CHEESE_SITE_FORWARD"] == "okcheese.com:18445"


def test_a_machine_on_the_public_base_is_told_nothing(monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "https://okcheese.com")

    assert "CHEESE_SITE_FORWARD" not in _env("https://okcheese.com/api")
