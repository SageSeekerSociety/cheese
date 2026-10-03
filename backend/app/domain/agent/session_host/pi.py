"""pi, as the session core starts and reads it.

The launch is the room's (`harness/pi/launch.py`): the pinned pi, its runner,
and the configuration the runner reads (`entry.py`), here rendered from a
``SessionSpec`` instead of a room's opening. Reading is the runner's journal,
translated into the platform's events by the room's assembler.
"""

import json

from app.domain.agent.harness import PI
from app.domain.agent.harness.pi.events import Assembler
from app.domain.agent.harness.pi.launch import (
    HostLaunch,
    arguments,
    extension,
    on_host,
    provider,
)
from app.domain.agent.service import AgentEvent
from app.domain.agent.session_host.contract import (
    Access,
    ModelSettings,
    SessionRef,
    SessionSpec,
)


def launch(ref: SessionRef, spec: SessionSpec, access: Access, api: str) -> HostLaunch:
    """The session as the session host is to start it, reaching the platform
    at ``api``."""
    settings = spec.model_settings
    args = arguments(spec.model)
    if not settings.thinking:
        args = [*args, "--thinking", "off"]
    host: dict = {
        "group_limit": spec.footprint.group_limit,
        "memory_max": f"{spec.footprint.memory_mb}M",
    }
    compaction = _compaction(settings)
    if compaction is not None:
        host["settings"] = compaction
    return on_host(
        state=ref.state,
        config={
            "opening": {
                "system_prompt": spec.system_prompt,
                "resume_token": spec.resume_token,
                "model": spec.model,
            },
            "args": args,
            "execution_target": access.target,
            "skills": {},
            "extension": extension(),
            "notice": "",
            "idle_exit_s": spec.idle_exit_s,
            "tools": {"names": list(spec.tools)},
        },
        api_base=api,
        model=spec.model,
        env={"CHEESE_API": api, "CHEESE_TOKEN": access.credential, **spec.env},
        models=_models(api, spec.model, settings),
        host=host,
    )


def _models(api: str, model: str, settings: ModelSettings) -> str | None:
    """The provider file, when the model is called otherwise than the
    provider's way (``provider``'s otherwise).

    The gateway's deepseek models think unless told not to, and a model that
    thinks spends its output on it; pi says so to them only for a model it
    knows can reason (``reasoning``) and in their dialect (``thinkingFormat``),
    with ``--thinking off`` on the command line choosing the off.
    """
    if (
        settings.thinking
        and settings.max_tokens is None
        and settings.context_tokens is None
    ):
        return None
    shape = json.loads(provider(api, model))
    cheese = shape["providers"]["cheese"]
    entry: dict = {"id": model}
    if not settings.thinking:
        cheese["compat"]["thinkingFormat"] = "deepseek"
        entry["reasoning"] = True
    if settings.max_tokens is not None:
        entry["maxTokens"] = settings.max_tokens
    if settings.context_tokens is not None:
        entry["contextWindow"] = settings.context_tokens
    cheese["models"] = [entry]
    return json.dumps(shape, ensure_ascii=False, indent=2)


def _compaction(settings: ModelSettings) -> str | None:
    """pi's settings file, when compaction is not pi's default."""
    if settings.reserve_tokens is None and settings.keep_tokens is None:
        return None
    compaction: dict = {}
    if settings.reserve_tokens is not None:
        compaction["reserveTokens"] = settings.reserve_tokens
    if settings.keep_tokens is not None:
        compaction["keepRecentTokens"] = settings.keep_tokens
    return json.dumps({"compaction": compaction})


class Translation:
    """One reader's journal records, as the platform's events."""

    def __init__(self) -> None:
        self._assembler = Assembler(harness=PI)

    def events(self, record: dict) -> list[AgentEvent]:
        return list(self._assembler.accept(record))
