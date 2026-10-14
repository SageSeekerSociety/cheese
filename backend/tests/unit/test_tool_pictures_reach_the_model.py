"""A tool's pictures reach the model in the harnesses that can show them.

A mini-app's save hands back the pictures the platform took of it, for the
writer to look at what it made (`sandbox/cheese`'s `Said`). The text alone is
what a caller that knows nothing of pictures reads; pi's runner and Codex's
tool server pass the pictures on beside it.
"""

import asyncio

from app.domain.agent.harness.codex.tools import PLATFORM, RemoteTools, platform_tools
from app.domain.agent.harness.pi import runner as pi_runner
from app.domain.agent.harness.pi.runner import Runner

cheese = platform_tools()
PICTURE = {"data": "d2lkZQ==", "mimeType": "image/png"}


def _saying_with_a_picture(tool, arguments, host):
    return cheese.Said("已保存小程序 k1 的第 1 版。", [PICTURE])


def test_codex_is_handed_the_pictures_after_the_text(monkeypatch):
    tools = RemoteTools({})
    tools.routes["cheese_app_save"] = (PLATFORM, "cheese_app_save")
    monkeypatch.setattr(tools.platform, "run_platform_tool", _saying_with_a_picture)

    answer = asyncio.run(
        tools(
            "item/tool/call",
            {"tool": "cheese_app_save", "callId": "c1", "arguments": {}},
        )
    )

    assert answer["success"] is True
    assert answer["contentItems"] == [
        {"type": "inputText", "text": "已保存小程序 k1 的第 1 版。"},
        {"type": "inputImage", "imageUrl": "data:image/png;base64,d2lkZQ=="},
    ]


def test_pis_runner_hands_the_extension_the_pictures(monkeypatch, tmp_path):
    runner = Runner(tmp_path / "pi")
    runner.tools = {"names": ["cheese_app_save"]}
    runner.platform_token = "token"
    monkeypatch.setattr(pi_runner.catalog, "run_platform_tool", _saying_with_a_picture)

    answer = runner._platform_tool("cheese_app_save", {}, "c1")

    assert answer["status"] == 0
    assert answer["stdout"] == "已保存小程序 k1 的第 1 版。"
    assert answer["images"] == [PICTURE]


def test_pi_is_told_which_models_can_look_at_pictures(monkeypatch):
    """pi hands a model that cannot read images only the text of a tool
    result: the deployment says which can, per model."""
    import json

    from app.core.config import settings
    from app.domain.agent.harness.pi.launch import provider

    monkeypatch.setattr(settings, "vision_models", {"sees"})
    [seeing] = json.loads(provider("https://x", "sees"))["providers"]["cheese"][
        "models"
    ]
    [blind] = json.loads(provider("https://x", "blind"))["providers"]["cheese"][
        "models"
    ]

    assert seeing["input"] == ["text", "image"]
    assert "image" not in blind.get("input", ["text"])
