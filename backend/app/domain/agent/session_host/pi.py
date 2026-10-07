"""pi, as the session core starts and reads it.

The launch is a Python program run on the session host (`harness/pi/launch.py`):
the pinned pi, its runner, and the configuration the runner reads
(`entry.py`), rendered from a ``SessionSpec``. The runner replaces a running pi
whose launch contract differs once it is idle. Reading is the runner's journal,
mirrored and translated into the platform's events (`harness/pi/subscription.py`).
"""

import base64
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.domain.agent.harness import HARNESSES, PI
from app.domain.agent.harness import SessionRef as Seat
from app.domain.agent.harness.claude_code import executor_launch
from app.domain.agent.harness.pi.launch import (
    HostLaunch,
    arguments,
    extension,
    on_host,
    provider,
)
from app.domain.agent.harness.pi.subscription import Subscription
from app.domain.agent.harness.prompt import SUBAGENT_RULES
from app.domain.agent.session_host.contract import (
    Access,
    Image,
    ModelSettings,
    SessionRef,
    SessionSpec,
)
from app.domain.agent.session_host.driver import (
    Launched,
    Readers,
    Wire,
    start_private_executor,
)

#: How long a launch may take: the first after a pin bump downloads pi, and
#: every one waits for the runner to answer (`host.STARTUP_S`).
LAUNCH_TIMEOUT_S = 900


def launch(ref: SessionRef, spec: SessionSpec, access: Access, api: str) -> HostLaunch:
    """The session as the session host is to start it, reaching the platform
    at ``api``."""
    settings = spec.model_settings
    args = arguments(spec.model)
    if not settings.thinking:
        args = [*args, "--thinking", "off"]
    host: dict = {}
    if spec.footprint is not None:
        host["group_limit"] = spec.footprint.group_limit
        host["memory_max"] = f"{spec.footprint.memory_mb}M"
    compaction = _compaction(settings)
    if compaction is not None:
        host["settings"] = compaction
    config: dict = {
        "opening": {
            "system_prompt": spec.system_prompt,
            "resume_token": spec.resume_token,
            "model": spec.model,
            "agent_handle": spec.acting,
        },
        "args": args,
        "execution_target": access.target,
        # Carried as content, not as paths: these are the platform's files,
        # and the session host has no copy of them. The runner writes them
        # and points pi at them.
        "skills": dict(spec.skills),
        "extension": extension(),
        "notice": spec.notice,
        # What every subagent the session starts is told (`pi/subagents.py`):
        # it reads none of the session's system prompt.
        "subagent_rules": SUBAGENT_RULES,
    }
    if spec.idle_exit_s is not None:
        config["idle_exit_s"] = spec.idle_exit_s
    if spec.tools is not None:
        config["tools"] = {"names": list(spec.tools)}
    return on_host(
        state=ref.state,
        config=config,
        api_base=api,
        model=spec.model,
        env=_env(spec, access, api),
        models=_models(api, spec.model, settings),
        host=host,
    )


def _env(spec: SessionSpec, access: Access, api: str) -> dict[str, str]:
    return {**spec.env, "CHEESE_API": api, "CHEESE_TOKEN": access.credential}


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


class PiDriver:
    harness = PI
    label = HARNESSES[PI].label
    read_failure = "pi entry read failed"
    # pi delivers it after the current tool calls finish and before the next
    # model call, which is the earliest point at which saying something can
    # still change what happens.
    steer = "steer"
    stop: tuple[str, str] = ("abort", "aborted")
    receipt_on_accept = True
    mirror = "entries.sqlite"

    def working(self, status: dict) -> bool:
        return bool(status.get("working"))

    def conversation(self, status: dict) -> str:
        return str(status.get("session_id") or "")

    def takes_inputs(self, status: dict) -> bool:
        return True

    async def launch(
        self,
        wire: Wire,
        host: str,
        ref: SessionRef,
        spec: SessionSpec,
        access: Access,
        known: Launched | None,
    ) -> Launched:
        api = await wire.api(host)
        await start_private_executor(
            wire,
            host,
            executor_launch,
            access.target,
            _env(spec, access, api),
            harness=self.label,
        )
        started = launch(ref, spec, access, api)
        if (
            known is not None
            and known.launch == started.contract
            and spec.resume_token in (None, "", known.conversation)
        ):
            # The runner that answered the last read was started with this
            # launch, so the host would only say so again.
            return known
        # The runner archive goes only to a state directory that lacks it: a
        # session's first start, or the first after a deploy.
        status = await wire.run(
            host,
            started.program(ship=False),
            timeout=LAUNCH_TIMEOUT_S,
            harness=self.label,
        )
        if status.get("runner") == "missing":
            status = await wire.run(
                host,
                started.program(ship=True),
                timeout=LAUNCH_TIMEOUT_S,
                harness=self.label,
            )
        return Launched(
            status["session_id"],
            frozenset(status.get("capabilities") or ()),
            status.get("contract", ""),
        )

    def subscription(
        self,
        seat: Seat,
        acting: str,
        mirror: Path,
        call: Callable[[str, dict], Awaitable[dict]],
        launched: Launched,
        readers: Readers,
    ) -> Subscription:
        return Subscription(
            seat,
            mirror,
            call,
            readers.consume,
            readers.activity,
            launched.conversation,
            moved=readers.moved,
        )

    def images(self, images: tuple[Image, ...]) -> list:
        """pi takes images inline, so the bytes travel with the message.

        Not also written to the machine's workspace: an agent that wants the
        file rather than the picture has no way to ask for it yet, and planting
        one it cannot be told about is a file nobody deletes.
        """
        return [
            {
                "data": base64.b64encode(image.data).decode(),
                "mimeType": image.media_type,
            }
            for image in images
        ]

    async def adopt(self, wire: Wire, found: list[tuple[str, Access]]) -> None:
        return None
