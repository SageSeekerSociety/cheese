"""The subagents a pi session starts: each a pi of its own, owned by the runner.

pi has no subagents. The platform's extension gives the session a ``Task`` tool
(`platform.ts`), and what that tool asks for is started here, beside the
session, on the same machine and in the same checkout: a second pi in RPC mode,
with its own context and its own session file, reaching the model through the
same provider as its parent. The runner is the one place that can do it —
the extension lives inside the parent's process and dies with it, while the
runner owns the log every reader of the room asks from.

What makes it the room's subagent rather than a process somebody started:

**Its model is admitted like any other.** A subagent's model is either the one
the parent named or the project's subagent default, and the platform decides
which, as it does for every native subagent (``/llm/admission``): the same
catalogue, the same tier policy, the same budget. A model the platform refuses
is a spawn that fails with the platform's words.

**Everything it writes goes into the session's own log.** Its entries are
pulled from its own pi the way the session's are, and written into the same
journal, each carrying which subagent it came from (``journal.THREAD``), so a
reader keeps them apart from the session's own turn.

**Its parent controls it and outlives it.** The parent says more to one that is
still running (``send``), stops one and leaves its siblings running (``stop``),
and when the parent's work is interrupted or its session ends, every subagent
it started ends with it (``stop_all``). One that has finished has handed back
its conclusion and is gone; more work means starting another one.
"""

import asyncio
import contextlib
import json
import urllib.request
import uuid
from pathlib import Path

from app.domain.agent.harness.driven.journal import PAGE
from app.domain.agent.harness.pi.rpc import LINE_LIMIT, Connection

#: The supply a pi can reach its model through. A subscription model is minted
#: for Claude Code alone (``Harness.carries_subscription``).
GATEWAY = "gateway"
#: How long a stopped subagent gets to wind down before it is killed.
STOP_GRACE_S = 5.0
#: What a parent that wants more from an ended subagent is told to do.
AGAIN = "要接着做，就重新起一个"
#: A live event after which pi has written an entry (``runner.SETTLES``).
SETTLES = frozenset({"message_end", "turn_end", "agent_end", "agent_settled"})


def admitted_model(api: str, token: str, requested: str | None) -> str:
    """The model the platform lets this subagent run, as the provider names it.

    Asked of ``/llm/admission`` — the question the metering proxy asks for a
    Claude Code subagent's every request — with the session's own credential:
    the named model if the project may run it, the project's subagent default
    when none is named. A refusal carries the platform's reason.
    """
    headers = {"Authorization": f"Bearer {token}", "X-Cheese-Subagent": "1"}
    if requested:
        headers["X-Cheese-Child-Model"] = requested
    request = urllib.request.Request(
        f"{api.rstrip('/')}/llm/admission", method="POST", headers=headers, data=b""
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read()).get("data") or {}
    if not data.get("allow"):
        raise ValueError(data.get("reason") or "the platform refused this subagent")
    supply = data.get("supply") or {}
    if supply.get("pool") != GATEWAY or not supply.get("model"):
        raise ValueError(
            f"{requested or 'the project subagent default'} is a subscription model, "
            "which only Claude Code can run; name a model the gateway serves"
        )
    return str(supply["model"])


def _assistant(entries: list[dict]) -> list[dict]:
    return [
        entry["message"]
        for entry in entries
        if entry.get("type") == "message"
        and (entry.get("message") or {}).get("role") == "assistant"
    ]


def last_words(entries: list[dict]) -> str:
    """What it last said, passing over a call that failed or was cut short."""
    for message in reversed(_assistant(entries)):
        if message.get("stopReason") in ("error", "aborted"):
            continue
        said = [
            part.get("text", "")
            for part in message.get("content") or []
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        if any(said):
            return "\n".join(text for text in said if text).strip()
    return ""


def closing_words(entries: list[dict]) -> tuple[str, str]:
    """How a run ended, from its last assistant entry: its status, and what it
    said last — or, for a run that failed, the error it stopped on."""
    messages = _assistant(entries)
    if messages and messages[-1].get("stopReason") == "error":
        return "failed", str(messages[-1].get("errorMessage") or "")
    return "completed", last_words(entries)


class Subagent:
    """One subagent, and the pi process running it while it runs."""

    def __init__(
        self,
        runner,
        agent_id: str,
        *,
        model: str,
        description: str,
        background: bool,
    ):
        self.runner = runner
        self.id = agent_id
        self.model = model
        self.description = description
        self.background = background
        self.home = runner.state / "subagents" / agent_id
        self.process: asyncio.subprocess.Process | None = None
        self.client: Connection | None = None
        self.listener: asyncio.Task | None = None
        self.errors = None
        self.status = "running"
        self.working = False
        # A message went in while the run was settling: a new run follows,
        # and the settle that was on its way does not end this subagent.
        self.continued = False
        self.lock = asyncio.Lock()
        self.pulling = asyncio.Lock()
        self.ended: asyncio.Future = asyncio.get_running_loop().create_future()
        self.seen: list[dict] = []

    # --- starting ----------------------------------------------------------

    def _configure(self) -> tuple[list[str], dict[str, str]]:
        """pi's argv and environment: the parent's, pointed at this model.

        The model is the only thing about the provider that differs, so the
        parent's `models.json` is copied with that one line changed, into a
        config directory of the subagent's own.
        """
        runner = self.runner
        args = list(runner.args)
        provider = args[args.index("--provider") + 1]
        config = self.home / "agent"
        config.mkdir(parents=True, exist_ok=True)
        parent = Path(runner.env["PI_CODING_AGENT_DIR"]) / "models.json"
        models = json.loads(parent.read_text(encoding="utf-8"))
        models["providers"][provider]["models"] = [{"id": self.model}]
        (config / "models.json").write_text(
            json.dumps(models, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        extension = runner.write_extension(
            runner.extension_files, runner.notice, home=self.home / "extension"
        )
        args[args.index("--model") + 1] = f"{provider}/{self.model}"
        # The session's system prompt is not passed on: a subagent is not the
        # room's agent. What it does get is the few rules every agent working
        # for the session needs (`prompt.SUBAGENT_RULES`), as a file for
        # the same reasons the session's prompt is one (`runner.start`).
        rules = []
        if runner.subagent_rules:
            prompt = self.home / "system-prompt.md"
            prompt.write_text(runner.subagent_rules, encoding="utf-8")
            rules = ["--append-system-prompt", str(prompt)]
        argv = [
            "--mode",
            "rpc",
            "--session-id",
            str(uuid.uuid4()),
            "--session-dir",
            str(self.home / "sessions"),
            *runner.skill_args,
            *rules,
            "--extension",
            str(extension / "index.ts"),
            *args,
        ]
        # Not `agent_env`: a subagent reports to its parent, never to the room,
        # so no person's message holds it to answering (`driven/runner.py`).
        env = {
            **runner.env,
            "PI_CODING_AGENT_DIR": str(config),
            "CHEESE_PI_EXTENSION": str(extension),
        }
        return argv, env

    async def start(self, prompt: str) -> None:
        argv, env = self._configure()
        self.errors = (self.home / "pi.log").open("ab")
        self.process = await asyncio.create_subprocess_exec(
            self.runner.binary,
            *argv,
            cwd=self.runner.cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self.errors,
            limit=LINE_LIMIT,
        )
        assert self.process.stdout is not None and self.process.stdin is not None
        self.client = Connection(
            self.process.stdout, self.process.stdin, on_event=self.observe
        )
        self.listener = asyncio.create_task(self.client.listen())
        await self.client.request("prompt", message=prompt)

    @property
    def thread(self) -> dict:
        return {"id": self.id}

    # --- reading -----------------------------------------------------------

    async def observe(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "agent_start":
            self.working = True
            self.continued = False
        elif kind == "agent_settled":
            self.working = False
            asyncio.ensure_future(self._finish())
        if kind in SETTLES:
            asyncio.ensure_future(self._pull_quietly())

    async def _pull_quietly(self) -> None:
        with contextlib.suppress(Exception):
            await self.pull()

    async def pull(self) -> None:
        """Land what this subagent's pi has written since its cursor, as
        records of its thread in the session's log."""
        if self.client is None or self.client.closed:
            return
        async with self.pulling:
            key = f"received:{self.id}"
            while True:
                since = self.runner.journal.recall(key)
                fields = {"since": since} if since is not None else {}
                page = (await self.client.request("get_entries", **fields))["entries"]
                if not page:
                    return
                self.seen += page
                self.runner.note_page(
                    [{**entry, "id": f"{self.id}/{entry['id']}"} for entry in page],
                    thread=self.thread,
                    cursor=(key, page[-1]["id"]),
                )
                if len(page) < PAGE:
                    return

    # --- ending ------------------------------------------------------------

    async def _finish(self) -> None:
        """A run settled: land its last entries, then end — unless a message
        arrived while it settled, which starts another run."""
        with contextlib.suppress(Exception):
            await self.pull()
        async with self.lock:
            if self.status != "running" or self.continued or self.working:
                return
            status, text = closing_words(self.seen)
            await self._end(status, text)
        if self.background:
            await self.runner.tell_parent(self.report())

    async def _end(self, status: str, text: str) -> None:
        self.status = status
        await self._terminate()
        if not self.ended.done():
            self.ended.set_result({"status": status, "text": text})

    async def _terminate(self) -> None:
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), STOP_GRACE_S)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.listener is not None:
            await asyncio.gather(self.listener, return_exceptions=True)
        if self.errors is not None:
            self.errors.close()

    def report(self) -> str:
        """What the parent is told when one it left running in the background
        has ended."""
        result = self.ended.result()
        words = result["text"] or "（没有留话）"
        return (
            f"后台分身 {self.id}（{self.description}）已结束，状态 {result['status']}。"
            f"它交回的结论：\n{words}"
        )

    # --- the parent's hand ---------------------------------------------------

    async def send(self, message: str) -> None:
        async with self.lock:
            if self.status == "stopped":
                # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
                raise ValueError(f"{self.id} 已经停了，不再接指令；{AGAIN}")
            if self.status != "running":
                # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
                raise ValueError(f"{self.id} 已经收工，结论已交回；{AGAIN}")
            assert self.client is not None
            if not self.working:
                self.continued = True
            # Mid-run it is read before the next model call; between runs it
            # starts the next one.
            await self.client.request(
                "prompt", message=message, streamingBehavior="steer"
            )

    async def stop(self) -> bool:
        """Stop this one. False when it had already ended."""
        async with self.lock:
            if self.status != "running":
                return False
            if self.client is not None and not self.client.closed:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(self.client.request("abort"), STOP_GRACE_S)
            with contextlib.suppress(Exception):
                await self.pull()
            await self._end("stopped", last_words(self.seen))
            return True


class Subagents:
    """The subagents one session has started, by id."""

    def __init__(self, runner):
        self.runner = runner
        self.started: dict[str, Subagent] = {}

    def _get(self, agent_id: str) -> Subagent:
        found = self.started.get(agent_id)
        if found is None:
            known = "、".join(self.started) or "（这个会话还没有起过分身）"
            # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
            raise ValueError(f"没有叫 {agent_id} 的分身；这个会话起过的：{known}")
        return found

    async def spawn(
        self,
        prompt: str,
        *,
        description: str,
        model: str | None,
        background: bool,
    ) -> dict:
        env = self.runner.env
        wire = await asyncio.to_thread(
            admitted_model,
            env.get("CHEESE_API", ""),
            env.get("CHEESE_TOKEN", ""),
            (model or "").strip() or None,
        )
        agent = Subagent(
            self.runner,
            f"sub-{uuid.uuid4().hex[:12]}",
            model=wire,
            description=description,
            background=background,
        )
        self.started[agent.id] = agent
        # What the session said up to the call that starts it lands first, so
        # the start follows the call in the log as it did on the machine.
        await self.runner.refresh()
        try:
            await agent.start(prompt)
        except Exception:
            await agent._end("failed", "")
            raise
        return {"agent_id": agent.id, "model": wire}

    async def wait(self, agent_id: str) -> dict:
        return await asyncio.shield(self._get(agent_id).ended)

    async def background(self, agent_id: str) -> None:
        """The call that started it stopped waiting on it: tell the parent when
        it ends, or now, if it already has."""
        agent = self._get(agent_id)
        agent.background = True
        if agent.ended.done():
            await self.runner.tell_parent(agent.report())

    async def send(self, agent_id: str, message: str) -> None:
        await self._get(agent_id).send(message)

    async def stop(self, agent_id: str) -> bool:
        return await self._get(agent_id).stop()

    async def stop_all(self) -> None:
        await asyncio.gather(
            *(agent.stop() for agent in self.started.values()),
            return_exceptions=True,
        )
