"""A tool's pictures reach the model in every harness, when the model reads them.

A mini-app's save hands back the pictures the platform took of it, for the
writer to look at what it made (`sandbox/cheese`'s `Said`). The text alone is
what a caller that knows nothing of pictures reads; pi's runner, Codex's tool
server and Claude Code's transport pass the pictures on beside it. Which models
read pictures is the gateway's to say (`supports_vision` on its model page),
never a list kept here.
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.domain.agent import gateway as gw
from app.domain.agent import gateway_catalog
from app.domain.agent.harness import PI
from app.domain.agent.harness.codex.tools import PLATFORM, RemoteTools, platform_tools
from app.domain.agent.harness.driven.runner import LONG_POLL
from app.domain.agent.harness.pi import runner as pi_runner
from app.domain.agent.harness.pi.launch import provider
from app.domain.agent.harness.pi.runner import Runner
from app.domain.agent.session_host.contract import Access, SessionRef, SessionSpec
from app.domain.agent.session_host.driver import Launched
from app.domain.agent.session_host.host import PICTURES, SessionHost, with_pictures

cheese = platform_tools()
PICTURE = {"data": "d2lkZQ==", "mimeType": "image/png"}


def _saying_with_a_picture(tool, arguments, host, *, delegated=False):
    return cheese.Said("已保存小程序 k1 的第 1 版。", [PICTURE])


@pytest.fixture(autouse=True)
def _forget_the_catalogue():
    gateway_catalog.reset()
    yield
    gateway_catalog.reset()


def _gateway(**vision: bool | None) -> gw.LlmGateway:
    """The gateway's model page: each model, with `supports_vision` as set
    there (None: never set)."""
    rows = [
        {
            "model_name": name,
            "litellm_params": {"model": f"openai/{name}"},
            "model_info": {} if sees is None else {"supports_vision": sees},
        }
        for name, sees in vision.items()
    ]
    return gw.LlmGateway(
        "http://gw",
        "mk",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": rows})
        ),
    )


def _gateway_says(**vision: bool | None) -> None:
    assert asyncio.run(gateway_catalog.refresh(_gateway(**vision)))


def test_a_sessions_tools_are_told_its_model_reads_pictures_as_the_gateway_says():
    _gateway_says(sees=True, blind=False, unmarked=None)

    def told(model: str) -> bool:
        spec = with_pictures(SessionSpec(system_prompt="", model=model))
        return spec.env.get(PICTURES) == "1"

    assert told("sees")
    assert not told("blind")
    assert not told("unmarked")
    # A model the gateway has not described, or a gateway never answered.
    assert not told("elsewhere")
    gateway_catalog.reset()
    assert not told("sees")


class _Machine:
    def is_online(self, device_id: str) -> bool:
        return True


class _Launching:
    """A harness that records the launches it is asked for."""

    harness = PI
    label = "pi"
    mirror = "journal"

    def __init__(self):
        self.launched: list[SessionSpec] = []

    async def launch(self, wire, host, ref, spec, access, known):
        self.launched.append(spec)
        return Launched("conversation", frozenset({LONG_POLL}), "launch")


@pytest.mark.anyio
async def test_a_session_started_on_a_model_that_reads_pictures_is_launched_saying_so(
    tmp_path,
):
    """What every harness's tools read (`cheese_app` among them) is the
    environment the session is launched with."""
    assert await gateway_catalog.refresh(_gateway(sees=True, blind=False))
    host = SessionHost(_Machine(), mirrors=tmp_path)
    harness = _Launching()
    host._drivers = {PI: harness}

    for model in ("sees", "blind"):
        await host.start(
            SessionRef(PI, f"session-{model}"),
            SessionSpec(system_prompt="", model=model, env={"CHEESE_TOPIC": "t"}),
            Access("token", host="machine"),
        )

    seeing, blind = harness.launched
    assert seeing.env == {"CHEESE_TOPIC": "t", PICTURES: "1"}
    assert blind.env == {"CHEESE_TOPIC": "t"}


def test_pi_is_told_which_models_can_look_at_pictures():
    """pi hands a model that cannot read images only the text of a tool
    result."""
    _gateway_says(sees=True, blind=False)
    [seeing] = json.loads(provider("https://x", "sees"))["providers"]["cheese"][
        "models"
    ]
    [blind] = json.loads(provider("https://x", "blind"))["providers"]["cheese"][
        "models"
    ]

    assert seeing["input"] == ["text", "image"]
    assert "image" not in blind.get("input", ["text"])


def test_codex_is_handed_the_pictures_after_the_text(monkeypatch):
    tools = RemoteTools({})
    tools.routes["cheese_app"] = (PLATFORM, "cheese_app")
    monkeypatch.setattr(tools.platform, "run_platform_tool", _saying_with_a_picture)

    answer = asyncio.run(
        tools(
            "item/tool/call",
            {"tool": "cheese_app", "callId": "c1", "arguments": {"action": "save"}},
        )
    )

    assert answer["success"] is True
    assert answer["contentItems"] == [
        {"type": "inputText", "text": "已保存小程序 k1 的第 1 版。"},
        {"type": "inputImage", "imageUrl": "data:image/png;base64,d2lkZQ=="},
    ]


def test_pis_runner_hands_the_extension_the_pictures(monkeypatch, tmp_path):
    """A session without hands (a document's) and one with the room's machine
    both pass them on."""
    monkeypatch.setattr(pi_runner.catalog, "run_platform_tool", _saying_with_a_picture)

    without_hands = Runner(tmp_path / "pi")
    without_hands.tools = {"names": ["cheese_app"]}
    without_hands.platform_token = "token"
    with_hands = Runner(tmp_path / "pi-machine")
    with_hands.machine = SimpleNamespace(client=None)

    for runner in (without_hands, with_hands):
        answer = asyncio.run(
            runner.run_cli("cheese_app", {"action": "save"}, None, "c1")
        )

        assert answer["status"] == 0
        assert answer["stdout"] == "已保存小程序 k1 的第 1 版。"
        assert answer["images"] == [PICTURE]
