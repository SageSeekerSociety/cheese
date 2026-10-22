"""What the authorization check asks about whatever was said: a command run on
the person's own device is always theirs to confirm, even when their rules
let everything else through."""

import asyncio

from app.domain.assistant import gate
from app.domain.assistant.models import AssistantRules


def test_running_a_command_on_the_persons_device_is_always_asked(monkeypatch):
    async def lets_everything_through(session, user_id):
        return AssistantRules(
            user_id=user_id, ask=[], allow_text="什么都直接做。", ask_text=""
        )

    async def no_model(session):
        raise AssertionError("nothing said makes it safe")

    monkeypatch.setattr(gate, "rules_of", lets_everything_through)
    monkeypatch.setattr(gate, "gatekeeper", no_model)
    action = gate.Action(
        "respond",
        "允许项目「课题组」在你的设备 laptop 上运行 MCP 服务器 files（命令：npx files）",
        always_ask="在你的设备上运行命令总要你确认",
    )

    verdict = asyncio.run(
        gate.judge(None, user_id=1, conversation_id=None, action=action)  # type: ignore[arg-type]
    )

    assert (verdict.allowed, verdict.stage) == (False, "rule")
    assert verdict.reason == "在你的设备上运行命令总要你确认"
