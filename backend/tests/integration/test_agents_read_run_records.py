"""An AI teammate working on the platform itself reads the platform's run
records with its tool — the same as the admin page — and one in any other
project is refused: the records name other projects and their conversations."""

import importlib.util
import uuid
from datetime import UTC, datetime
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.run_record.models import RunRecord
from tests.integration.conftest import (
    post_project,
    room_agent_headers,
    session_auth_headers,
)

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load_tools():
    loader = SourceFileLoader("cheese_platform_tools_run_records", str(_CHEESE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cheese = _load_tools()
DEV = "rr-dev"


def _room(client, project: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project, "title": "查报错"},
        headers=session_auth_headers(DEV),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


class AgentHost:
    def __init__(self, client, project: str, topic: str):
        self.client = client
        self.environ = {"CHEESE_TOPIC": topic, "CHEESE_PROJECT": project}
        self.headers = room_agent_headers(client, topic)

    def request(self, plan):
        response = self.client.request(
            plan["method"], plan["path"], json=plan.get("body"), headers=self.headers
        )
        if not 200 <= response.status_code < 300:
            raise cheese.PlatformHTTPError(response.status_code, response.text)
        return response.json()

    def run(self, **args) -> str:
        return cheese.run_platform_tool("cheese_run_records", args, self)


@pytest.fixture
def projects(client, monkeypatch) -> tuple[str, str]:
    """The platform's own project, and one that is not."""
    platform, other = (
        post_project(
            client, json={"name": name}, headers=session_auth_headers(DEV)
        ).json()["data"]["id"]
        for name in ("知是", "别的")
    )
    monkeypatch.setattr(
        settings, "docs_dev_repositories", [f"project-{uuid.UUID(platform).hex}/code"]
    )

    async def keep() -> None:
        async with client.test_factory() as s:
            s.add(
                RunRecord(
                    project_id=uuid.UUID(other),
                    kind="backend_error",
                    severity="error",
                    content="后端报错（GET /projects/x/site）：ConnectError",
                    meta={"fingerprint": "fp-site", "stack": "Traceback…ConnectError"},
                    created_at=datetime.now(UTC),
                )
            )
            await s.commit()

    client.portal.call(keep)
    return platform, other


def test_an_agent_on_the_platform_reads_the_errors_and_one_of_them_whole(
    client, projects
):
    platform, _ = projects
    agent = AgentHost(client, platform, _room(client, platform))

    listed = agent.run(group="errors")
    assert "ConnectError" in listed
    whole = agent.run(key="fp-site", kind="backend_error")
    assert "Traceback" in whole
    assert "别的" in whole


def test_an_agent_elsewhere_is_refused(client, projects):
    _, other = projects
    agent = AgentHost(client, other, _room(client, other))

    with pytest.raises(cheese.PlatformToolError) as refusal:
        agent.run()
    assert "知是代码仓库" in str(refusal.value)
