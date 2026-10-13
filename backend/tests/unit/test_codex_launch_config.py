import tomllib

import pytest

from app.domain.agent.harness import CODEX
from app.domain.agent.harness.codex import launch_identity
from app.domain.agent.session_host import codex as codex_driver
from app.domain.agent.session_host.codex import CodexDriver
from app.domain.agent.session_host.contract import Access, SessionRef, SessionSpec


class Wire:
    def __init__(self):
        self.launch = None

    async def api(self, host):
        return "http://api"

    async def run(self, host, script, **kwargs):
        return {"thread_id": "thread", "capabilities": []}


@pytest.mark.anyio
async def test_codex_launch_sets_the_native_instruction_limit(monkeypatch):
    captured = {}

    async def start_private_executor(*args, **kwargs):
        return None

    def runner_script(**kwargs):
        captured.update(kwargs)
        return "runner"

    monkeypatch.setattr(codex_driver, "start_private_executor", start_private_executor)
    monkeypatch.setattr(codex_driver, "script", runner_script)
    monkeypatch.setattr(codex_driver, "launch_identity", launch_identity)

    wire = Wire()
    result = await CodexDriver().launch(
        wire,
        "host",
        SessionRef(harness=CODEX, home="session"),
        SessionSpec(system_prompt="room", model="fixture"),
        Access(credential="token", target={"workspace": "/repo"}),
        None,
    )

    assert result.conversation == "thread"
    config = tomllib.loads(captured["codex_config"])
    assert config["project_doc_max_bytes"] == 32768
    assert "project_doc_max_bytes" not in config["model_providers"]["cheese"]
