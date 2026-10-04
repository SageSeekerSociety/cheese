"""Codex, as the session core starts and reads it.

The launch is the runner archive and a Python program run on the session host
(`harness/codex/launch.py`); the host keeps a running runner whose execution
target, teammate and model are the launch's (``launch_identity``). Reading is
the runner's journal of app-server events (`harness/codex/subscription.py`).
"""

import base64
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.domain.agent.harness import CODEX
from app.domain.agent.harness import SessionRef as Seat
from app.domain.agent.harness.claude_code import executor_launch
from app.domain.agent.harness.codex import Subscription, launch_identity, script
from app.domain.agent.session_host.contract import (
    Access,
    Image,
    SessionRef,
    SessionSpec,
)
from app.domain.agent.session_host.driver import (
    Launched,
    Readers,
    Wire,
    start_private_executor,
)

BINARY = "~/.cheese/tools/codex/node_modules/.bin/codex"


class CodexDriver:
    harness = CODEX
    label = "Codex"
    read_failure = "Codex journal read failed"
    # The runner's ``send`` becomes ``turn/steer`` while a turn is open.
    steer = "send"
    stop: tuple[str, str] = ("interrupt", "interrupted")
    receipt_on_accept = True
    mirror = "events.sqlite"

    def working(self, status: dict) -> bool:
        return bool(status.get("turn_id"))

    def conversation(self, status: dict) -> str:
        return str(status.get("thread_id") or "")

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
        env = {**spec.env, "CHEESE_API": api, "CHEESE_TOKEN": access.credential}
        await start_private_executor(
            wire, host, executor_launch, access.target, env, harness=self.label
        )
        config = {
            "binary": BINARY,
            "opening": {
                "system_prompt": spec.system_prompt,
                "resume_token": spec.resume_token,
                "model": spec.model,
                "agent_handle": spec.acting,
            },
            "execution_target": access.target,
            # The platform's own skills, as content: the runner writes them
            # where the session's Codex reads them (`tools.ship_skills`).
            "skills": dict(spec.skills),
        }
        identity = launch_identity(config)
        if (
            known is not None
            and known.launch == identity
            and spec.resume_token in (None, "", known.conversation)
        ):
            # The runner that answered the last read was ensured with this
            # launch, so the host would only say so again.
            return known
        codex_config = (
            'model_provider = "cheese"\n'
            '[model_providers.cheese]\nname = "Cheese"\n'
            f"base_url = {json.dumps(api + '/llm/v1')}\n"
            'wire_api = "responses"\nenv_key = "CHEESE_TOKEN"\n'
            "requires_openai_auth = false\n[analytics]\nenabled = false\n"
        )
        launch = {
            "state": ref.state,
            "config": config,
            "codex_config": codex_config,
            "env": env,
        }
        status = await wire.run(
            host, script(**launch, ship=False), timeout=120, harness=self.label
        )
        if status.get("runner") == "missing":
            status = await wire.run(
                host, script(**launch, ship=True), timeout=120, harness=self.label
            )
        return Launched(
            status["thread_id"],
            frozenset(status.get("capabilities") or ()),
            identity,
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
            seat, mirror, call, readers.consume, readers.activity, moved=readers.moved
        )

    def images(self, images: tuple[Image, ...]) -> list:
        """Codex takes images inline, as data URLs, so the bytes travel with
        the message; nothing is written to the machine."""
        return [
            f"data:{image.media_type};base64,{base64.b64encode(image.data).decode()}"
            for image in images
        ]

    async def adopt(self, wire: Wire, found: list[tuple[str, Access]]) -> None:
        return None
