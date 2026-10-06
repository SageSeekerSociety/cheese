"""Own a pi process on the session host, and outlive whoever is reading from it.

The backend is the thing that gets replaced. pi is not, and neither is this —
so the session, its entries and the record of which input was already accepted
all live here, and a backend that comes back asks from a cursor instead of
starting over.

Two decisions this makes rather than discovers:

**We choose the session id.** ``pi --session-id`` takes one and creates the
session if it is missing, so resuming is passing the same id again rather than
finding out what pi called it and storing that somewhere. One less piece of
state that can be lost, and a resume that works on a machine whose session
directory was wiped.

**Entries are pulled, not caught.** The live event stream says something
happened; ``get_entries since=`` says what, with ids that make asking twice
harmless. So the stream is a doorbell and the journal is the record, which is
why a missed event costs nothing.
"""

import asyncio
import base64
import contextlib
import json
import os
import shlex
import shutil
import sys
import time
import uuid
from pathlib import Path

from app.domain.agent.executor_transport import PlatformHost, RemoteClient
from app.domain.agent.harness.driven import runner
from app.domain.agent.harness.driven.journal import PAGE
from app.domain.agent.harness.driven.runner import SessionStart, socket_path
from app.domain.agent.harness.pi import catalog, hooks
from app.domain.agent.harness.pi.jobs import Jobs
from app.domain.agent.harness.pi.journal import (
    COMPACTING,
    GAVE_UP,
    RETRYING,
    THREAD,
    Journal,
)
from app.domain.agent.harness.pi.machine import Machine
from app.domain.agent.harness.pi.mcp import ProjectServers
from app.domain.agent.harness.pi.rpc import LINE_LIMIT, Connection
from app.domain.agent.harness.pi.subagents import Subagents
from app.domain.agent.nonce import nonce_in

# A live event that can only mean an entry was written. Anything else is
# progress within a message, and the entry for it does not exist yet.
SETTLES = frozenset({"message_end", "turn_end", "agent_end", "agent_settled"})

#: The tools that only read, which a session reading the room's machine has
#: (`_start_without_hands`): pi's own, and the checkout's history (`git`).
READING_TOOLS = ("read", "ls", "find", "grep", "git")


class Runner(runner.Runner[Journal]):
    def __init__(self, state: Path, *, idle_exit_s: float = runner.IDLE_EXIT_S):
        super().__init__(state, Journal, "entries.sqlite", idle_exit_s=idle_exit_s)
        self.client: Connection | None = None
        self.refreshing = asyncio.Lock()
        self.doorbell = asyncio.Event()
        self.refresher: asyncio.Task | None = None
        self.working = False
        # 读到的是哪一版实况文档，写回时要出示（平台工具表的 `_doc_get`）。
        self.doc_versions: dict[str, int] = {}
        # What the live stream said about a failed model call, waiting to be
        # written after the entry it is about (see ``refresh``).
        self.verdicts: list[dict] = []
        # The failed call those verdicts are about: its message's timestamp,
        # which is also on the entry pi wrote for it.
        self.last_failure: object = None
        # Whether a failed call waits for its verdict before it is written
        # down (see ``refresh``). Off while the session's past is read in.
        self.holding = False
        # A failed call is being retried and nothing has concluded it yet.
        self.failing = False
        # The error of a failed call pi said it will not retry. pi decides
        # whether to compact and ask it again only after it has reported the
        # run over (a context overflow), so the turn ends on it once pi has
        # settled, and not before.
        self.unretried: str | None = None
        # Failed calls pi asked again after compacting: they wait for no verdict.
        self.asked_again: set[object] = set()
        # The work a compaction under way started under (``owner``).
        self.compacting_for: dict | None = None
        # The platform asked pi to stop: a retry cut short by it is not a
        # failure of anything.
        self.aborting = False
        # The runner's own turn that holds the session to a reply, and the
        # marker platform instructions carry in this room (`start`).
        self.continuing: asyncio.Task | asyncio.Future | None = None
        self.notice = ""
        # What every subagent the session starts reads as its system prompt's
        # tail (`subagents.py`); the platform's words, handed over (`start`).
        self.subagent_rules = ""
        # The project's MCP servers (`mcp.py`), opened with the session.
        self.servers: ProjectServers | None = None
        self.mcp_tools: list[dict] = []
        # The room's machine, where every file and command of the session's is
        # (`machine.py`), and the checkout as the session sees it there. `cwd`
        # is the directory pi runs in on this host, which holds nothing of the
        # project; `env` is pi's environment here.
        self.machine: Machine | None = None
        self.jobs: Jobs | None = None
        self.workspace = ""
        self.cwd = ""
        self.env: dict[str, str] = {}
        # What the repository says about itself (`repository.py`), read once the
        # session is on its machine and added to every turn's prompt.
        self.context = ""
        # What a subagent is started from (`subagents.py`): the parent's own
        # pin, argv, skills and extension, as `start` was given them.
        self.binary = ""
        self.args: list[str] = []
        self.model = ""
        self.skill_args: list[str] = []
        self.extension_files: dict[str, str] = {}
        # A session with no hands (`_start_without_hands`): the table tools it
        # has (`names`), and the credential the question being answered was
        # given, which each of them calls the platform with.
        self.tools: dict = {}
        self.platform_token = ""
        self.children = Subagents(self)
        # The blocks of the assistant message pi is writing, by its content
        # index (``write``), and how many messages ended and how many of them
        # have had their entry pulled since: what is shown goes then (``refresh``).
        self.writing: dict[int, dict] = {}
        self.messages_ended, self.cleared = 0, 0

    capabilities = (runner.LONG_POLL, runner.LIVE)

    # --- reading -------------------------------------------------------------

    async def observe(self, event: dict) -> None:
        """Ring the doorbell; never answer it here.

        This runs inside the read loop, and fetching entries is a request only
        that same loop can answer — doing it here is a process waiting on
        itself. So the reader only records that something happened.
        """
        kind = event.get("type")
        if kind == "message_update":
            self.write(event.get("assistantMessageEvent") or {})
        elif kind == "message_start":
            if (event.get("message") or {}).get("role") == "assistant":
                self.writing = {}
                owner = json.loads(self.journal.recall("owner") or "{}")
                self.show([], owner.get("work_id"))
        elif kind in ("message_end", "agent_end"):
            self.messages_ended += 1
        if kind == "agent_start":
            self.working = True
        elif kind == "agent_settled":
            self.working = False
            # pi delivers a steered message before its next model call and
            # settles only once none is left, so nothing unread survives this.
            # A person it left unanswered gets one more turn, unless the
            # platform is the one that stopped it.
            reason = None if self.aborting else self.insist()
            if reason is None:
                self.reply_settled()
            else:
                self.continuing = asyncio.ensure_future(self._hold_to_reply(reason))
        elif kind == "auto_retry_start":
            self.failing = True
            self._verdict(
                RETRYING,
                after=self.last_failure,
                attempt=event.get("attempt"),
                maxAttempts=event.get("maxAttempts"),
                delayMs=event.get("delayMs"),
                errorMessage=event.get("errorMessage"),
            )
        elif kind == "auto_retry_end":
            if event.get("success"):
                self.failing = False
            elif self.failing:
                # A retry cut short while it waited: no agent_end follows.
                self._give_up(event.get("finalError"))
        elif kind == "compaction_start":
            # pi also compacts after a turn has answered, so the room may send
            # the next input before this ends. Both ends belong to the work it
            # started under, or the room's line for it never closes. That work
            # is named at IMPORT, not here: the live event can arrive before
            # the refresh that walks this turn's user entry, and the owner the
            # journal knew then is the previous turn's (FB-56 — the live
            # stream and the journal are two timelines).
            self._verdict(COMPACTING, after=None, done=False)
        elif kind == "compaction_end":
            self._verdict(
                COMPACTING,
                after=None,
                done=True,
                aborted=bool(event.get("aborted")),
                errorMessage=str(event.get("errorMessage") or ""),
            )
            if event.get("willRetry") and self.unretried is not None:
                # The call that overflowed is asked again on the compacted
                # context: it is not how the turn ends.
                self.unretried = None
                self.asked_again.add(self.last_failure)
        elif kind == "agent_end":
            # The call that ended this run, if it failed. When pi is not trying
            # again — a request it cannot retry, or the retries ran out — that
            # failure is how the turn ends.
            last = next(
                (
                    message
                    for message in reversed(event.get("messages") or [])
                    if isinstance(message, dict) and message.get("role") == "assistant"
                ),
                None,
            )
            if last is not None and last.get("stopReason") == "error":
                self.last_failure = last.get("timestamp")
                if not event.get("willRetry"):
                    self.unretried = str(last.get("errorMessage") or "")
        if kind == "agent_settled" and self.unretried is not None:
            self._give_up(self.unretried)
        if kind in SETTLES:
            self.doorbell.set()

    def write(self, update: dict) -> None:
        """Show the assistant message pi is writing, block by block, as its
        deltas arrive (`message_update`): text, and tool calls with their
        arguments as raw JSON so far. Reasoning is not shown."""
        kind, index = update.get("type"), update.get("contentIndex")
        if not isinstance(index, int):
            return
        if kind == "text_start":
            self.writing[index] = {"type": "text", "text": ""}
        elif kind == "toolcall_start":
            self.writing[index] = {
                "type": "tool",
                "id": update.get("id"),
                "name": update.get("toolName"),
                "arguments": "",
            }
        elif kind in ("text_delta", "text_end", "toolcall_delta"):
            block = self.writing.get(index)
            if block is None:
                return
            if kind == "text_delta":
                block["text"] += str(update.get("delta") or "")
            elif kind == "text_end":
                block["text"] = str(update.get("content") or block["text"])
            else:
                block["arguments"] += str(update.get("delta") or "")
        else:
            return
        self.live_blocks = [self.writing[i] for i in sorted(self.writing)]
        self.shown()

    def _verdict(self, kind: str, **fields) -> None:
        self.verdicts.append({"type": kind, "id": f"cheese:{uuid.uuid4()}", **fields})
        self.doorbell.set()

    def _give_up(self, error: object) -> None:
        self.failing = False
        self.unretried = None
        self._verdict(
            GAVE_UP,
            after=self.last_failure,
            errorMessage=str(error or ""),
            aborted=self.aborting,
        )
        self.aborting = False

    async def _answer_the_door(self) -> None:
        while True:
            await self.doorbell.wait()
            self.doorbell.clear()
            try:
                await self.refresh()
            except Exception:
                # pi going away is the read loop's news to break, not ours;
                # a failed pull is retried on the next event or socket call.
                await asyncio.sleep(0.2)

    async def refresh(self) -> None:
        """Pull whatever pi has written since our cursor and stamp it.

        The stamp is the work in flight, and it is applied here because this is
        the only place that knows: an entry produced after one input and before
        the next belongs to that input's turn, and nothing in the entry says so.
        """
        if self.client is None:
            return
        async with self.refreshing:
            # pi wrote a message's entry before it said the message ended, so
            # this pull lands it, and what was shown of it goes.
            ended = self.messages_ended
            # A failed call is held back until pi has said what it will do
            # about it (a verdict names it by ``after``), then goes in with the
            # verdict right behind it — so the log reads failure, verdict, next
            # attempt, whatever order the pulls happen to run in. What pi wrote
            # after a held entry waits with it: the cursor stays before it.
            verdicts, self.verdicts = self.verdicts, []
            owner = json.loads(self.journal.recall("owner") or "{}")
            # A compaction names no entry: it is news the moment pi says it,
            # and a failed call held back for its verdict must not hold it too.
            loose = [v for v in verdicts if v["type"] == COMPACTING]
            verdicts = [v for v in verdicts if v["type"] != COMPACTING]

            current_mark = owner
            page_owner: str | None = None

            def stamped(record: dict) -> dict:
                # A compaction's records carry the work it started under.
                nonlocal current_mark
                message = record.get("message") or {}
                if (
                    record.get("type") == "message"
                    and not record.get(THREAD)
                    and message.get("role") == "user"
                ):
                    # A native user entry is where the next turn's owner
                    # becomes known: the input's own marker names it exactly.
                    # Entries before it stay the previous work's, from it on
                    # they are the new one's (FB-56) — never the whole page
                    # the latest owner.
                    content = message.get("content") or []
                    entry_text = "".join(
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict) and part.get("type") == "text"
                    )
                    nonce = nonce_in(entry_text)
                    work = self.journal.recall(f"input:{nonce}") if nonce else None
                    if work:
                        base = json.loads(self.journal.recall("owner") or "{}")
                        base.pop("unsolicited", None)
                        current_mark = {**base, "work_id": work}
                        # The owner moves at consumption, and durably: a
                        # restart mid-page re-reads this, while a send that
                        # was never consumed never moved it at all.
                        # The owner moves at consumption — and lands only
                        # with the page that proves it (FB-56): written by
                        # `import_entries` in the same transaction as the
                        # entries and the cursor, never on its own.
                        nonlocal page_owner
                        page_owner = json.dumps(current_mark)
                if record.get("type") == COMPACTING and not record.get("done"):
                    # One compaction, one work: the start's mark at import
                    # decides, and the end follows it even when a new input
                    # has moved the owner since (FB-56) — never two works
                    # for the same compaction.
                    self.compacting_for = record.get("cheese") or current_mark
                    return (
                        {**record, "cheese": self.compacting_for}
                        if self.compacting_for
                        else record
                    )
                if record.get("type") == COMPACTING and record.get("done"):
                    mark = record.get("cheese") or self.compacting_for or current_mark
                    self.compacting_for = None
                    return {**record, "cheese": mark} if mark else record
                mark = record.get("cheese") or current_mark
                return {**record, "cheese": mark} if mark else record

            held = False
            while not held:
                since = self.journal.recall("received")
                # A fresh session has no cursor, and pi REFUSES a null one
                # ("Entry not found: null") rather than reading it as "from the
                # start" — so the first pull asks without the field at all.
                fields = {"since": since} if since is not None else {}
                page = (await self.client.request("get_entries", **fields))["entries"]
                rows: list[dict] = []
                for entry in page:
                    message = entry.get("message") or {}
                    at = message.get("timestamp")
                    behind = [
                        v for v in verdicts if at is not None and v["after"] == at
                    ]
                    if (
                        self.holding
                        and message.get("stopReason") == "error"
                        and not behind
                        and at not in self.asked_again
                    ):
                        held = True
                        break
                    rows.append(stamped(entry))
                    rows += [stamped(v) for v in behind]
                    verdicts = [v for v in verdicts if v not in behind]
                if held:
                    rows += [stamped(v) for v in loose]
                    loose = []
                if rows:
                    self.journal.import_entries(rows, owner=page_owner)
                if len(page) < PAGE:
                    break
            if loose:
                self.journal.import_entries([stamped(v) for v in loose])
            # Not placed yet: the entry each names is still to be pulled.
            self.verdicts = verdicts + self.verdicts
            if ended > self.cleared:
                self.cleared = ended
                self.writing = {}
                self.show([], None)

    # --- the platform extension ----------------------------------------------

    def write_extension(
        self,
        files: dict[str, str],
        notice: str = "",
        *,
        home: Path | None = None,
        tools: list[dict] | None = None,
    ) -> Path:
        """Put the extension and its tool catalog on disk; answer with the entry.

        ``home`` is a subagent's (`subagents.py`): the same extension with the
        platform's tools, the project's MCP servers and its hooks, and none of
        what belongs to the session alone — no subagents of its own, and no
        background jobs, which would outlive the subagent that started them with
        nobody left to be told they ended.

        ``tools`` is a session with no hands (`_start_without_hands`): those
        tools and nothing else, and no machine behind any of them.
        """
        child = home is not None
        hands = tools is None
        home = home or self.state / "extension"
        home.mkdir(parents=True, exist_ok=True)
        for name, content in sorted(files.items()):
            (home / name).write_text(content, encoding="utf-8")
        reason = ""
        if tools is None:
            try:
                tools = catalog.tools()
            except Exception as error:  # noqa: BLE001 — a room still opens without them
                tools, reason = [], f"{type(error).__name__}: {error}"
        (home / "platform.json").write_text(
            json.dumps(
                {
                    "socket": socket_path(self.state),
                    "state": str(self.state),
                    "hands": hands,
                    # A session without hands that reads the room's machine.
                    "reading": not hands and self.machine is not None,
                    # The checkout as the session sees it on the room's machine:
                    # where pi's own tools resolve a path and run a command.
                    "workspace": self.workspace,
                    "jobs": "" if child or not hands else str(self.state / "bg"),
                    "subagents": hands and not child,
                    "tools": tools,
                    "unavailable": reason,
                    # The project's MCP servers' tools, listed when the session
                    # opened (`open_servers`); each call comes back as `mcp`.
                    "mcp": self.mcp_tools,
                    # The marker every platform instruction in this room already
                    # carries, handed over rather than restated: it is the one
                    # string in a prompt that claims institutional authority,
                    # and a second copy of it is a copy that drifts.
                    "notice": notice,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return home

    async def open_servers(self) -> None:
        """List every MCP server's tools, before the extension's manifest is
        written."""
        assert self.machine is not None
        self.servers = ProjectServers(
            self.machine, self.journal.recall("session_id") or ""
        )
        self.mcp_tools = await self.servers.discover()

    def _invoke(self, payload: dict, args: dict) -> dict:
        """A command on the machine, answered the way an executor's Bash is:
        what the platform's tools need from the machine (`PlatformHost`)."""
        assert self.machine is not None
        code, output = self.machine.run(args["command"])
        text = output.decode("utf-8", "replace")
        return {"value": {"stdout": text if code == 0 else f"Exit code {code}\n{text}"}}

    async def run_cli(
        self, tool: str, arguments: dict, cwd: str | None, call_id: str
    ) -> dict:
        """One platform tool call.

        A tool from the platform's table runs here against the backend, reaching
        the machine only for what it needs from it. Anything else is a CLI
        command, run on the machine as the CLI would have been typed there:
        argparse is the authority twice over — it says what the arguments mean,
        and ``catalog.argv`` re-parses what it built, so a call that could not
        have been typed fails here rather than reaching the CLI as a malformed
        command line.

        A session with no hands, or one that only reads the machine, has
        neither: each of its tools is one request to the platform, under the
        tools' path and the session's own credential.
        """
        if self.machine is None or self.tools:
            return await asyncio.to_thread(
                self._platform_tool, tool, arguments, call_id
            )
        if catalog.is_platform_tool(tool):
            host = PlatformHost(
                self.machine.client, self._invoke, call_id, self.doc_versions
            )
            try:
                text = await asyncio.to_thread(
                    catalog.run_platform_tool, tool, arguments, host
                )
            except Exception as error:  # noqa: BLE001 — the agent reads the reason
                return {"status": 1, "stdout": "", "stderr": str(error)}
            return {"status": 0, "stdout": text, "stderr": ""}
        command = shlex.join([catalog.CLI, *catalog.argv(tool, arguments)])
        code, output = await asyncio.to_thread(
            self.machine.run, f"exec {command} </dev/null", cwd=cwd
        )
        return {
            "status": code,
            "stdout": output.decode("utf-8", "replace"),
            "stderr": "",
        }

    def _platform_tool(self, tool: str, arguments: dict, call_id: str) -> dict:
        if tool not in (self.tools.get("names") or []):
            return {"status": 1, "stdout": "", "stderr": f"{tool}: no such tool"}
        if not self.platform_token:
            # Never the credential the session started with: that one is the
            # room's, and a tool acts for whoever asked.
            return {
                "status": 1,
                "stdout": "",
                "stderr": "No question is being answered",
            }
        host = PlatformHost(
            RemoteClient({"platform_token": self.platform_token}),
            None,
            call_id,
            self.doc_versions,
        )
        try:
            text = catalog.run_platform_tool(tool, arguments, host)
        except Exception as error:  # noqa: BLE001 — the agent reads the reason
            return {"status": 1, "stdout": "", "stderr": str(error)}
        return {"status": 0, "stdout": text, "stderr": ""}

    async def tool_hooks(self, params: dict) -> dict:
        """The project's hooks for one event of one of pi's own tool calls,
        asked by the extension before the call and after it (`platform.ts`).

        A deny is an answer, not a failure: the extension blocks the call with
        it, or adds it to the result. A hook that could not run is a failure,
        and pi blocks a call whose `tool_call` handler throws.
        """
        assert self.machine is not None
        try:
            args = await hooks.run(
                params["event"],
                params["tool"],
                params.get("input") or {},
                call_id=params["id"],
                machine=self.machine,
                cwd=params.get("cwd"),
                result=params.get("result"),
            )
        except hooks.Denied as denied:
            return {"denied": str(denied)}
        return {"input": args}

    # --- the machine, for pi's own tools ---------------------------------------

    async def files(self, params: dict) -> dict:
        """One of the file operations pi's own tools are built on
        (`platform.ts`), on the room's machine; the few files the session
        keeps on this host are read here (`Machine.local`)."""
        assert self.machine is not None
        machine, path = self.machine, params["path"]
        operation = params["operation"]
        if operation == "read":
            data = await asyncio.to_thread(machine.read_file, path)
            return {"data": base64.b64encode(data).decode()}
        if operation == "access":
            await asyncio.to_thread(
                machine.access, path, write=bool(params.get("write"))
            )
            return {}
        if operation == "image":
            return {"type": await asyncio.to_thread(machine.image_type, path)}
        more = {k: v for k, v in params.items() if k not in ("operation", "path")}
        return await asyncio.to_thread(machine.files, operation, path, **more)

    async def shell(self, params: dict) -> dict:
        """pi's shell, as its bash runs it (`platform.ts`): a command started on
        the machine, then read from an offset until it has ended."""
        assert self.machine is not None
        machine = self.machine
        operation = params["operation"]
        if operation == "start":
            command_id = "pi-" + uuid.uuid4().hex
            await asyncio.to_thread(
                machine.start,
                command_id,
                # As the executor's own Bash runs one for Codex: in the user's
                # shell, from the snapshot of their profile.
                f"eval {shlex.quote(params['command'])} </dev/null",
                cwd=params.get("cwd"),
                shell=True,
            )
            return {"id": command_id}
        if operation == "read":
            read = await asyncio.to_thread(
                machine.read,
                params["id"],
                int(params.get("offset") or 0),
                wait=float(params.get("wait") or 1.0),
            )
            answer = {
                "data": base64.b64encode(read["data"]).decode(),
                "offset": read["offset"],
            }
            if "exit" in read:
                answer["exit"] = read["exit"]
            if read.get("lost"):
                answer["lost"] = True
            return answer
        if operation == "signal":
            running = await asyncio.to_thread(
                machine.signal, params["id"], int(params["signal"])
            )
            return {"running": running}
        raise ValueError(f"Unknown shell operation: {operation}")

    # --- subagents -------------------------------------------------------------

    def _stamped(self, record: dict, thread: dict) -> dict:
        owner = json.loads(self.journal.recall("owner") or "{}")
        stamped = {**record, THREAD: thread}
        return {**stamped, "cheese": owner} if owner else stamped

    def note(self, record: dict, *, thread: dict) -> None:
        """A record of the runner's own about one subagent (`subagents.py`)."""
        self.journal.import_entries(
            [self._stamped({**record, "id": f"cheese:{uuid.uuid4()}"}, thread)]
        )

    def note_page(
        self, entries: list[dict], *, thread: dict, cursor: tuple[str, str]
    ) -> None:
        """A page of one subagent's own entries, landed on its thread."""
        self.journal.import_entries(
            [self._stamped(entry, thread) for entry in entries], cursor=cursor
        )

    async def tell_parent(self, text: str) -> None:
        """Say something to the session on the platform's behalf: after what it
        is doing if it is working, as a turn of its own if it is not — the way
        Claude Code hears that a subagent it left in the background ended."""
        if self.client is None or self.client.closed:
            return
        if not self.working:
            owner = json.loads(self.journal.recall("owner") or "{}")
            owner.update(work_id=str(uuid.uuid4()), unsolicited=True)
            self.journal.remember("owner", json.dumps(owner))
        try:
            await self.client.request(
                "prompt",
                message=f"{self.notice}\n{text}" if self.notice else text,
                streamingBehavior="followUp",
            )
        except Exception as error:  # noqa: BLE001 — the record is in the log
            print(f"could not tell the session: {error!r}", file=sys.stderr)

    # --- lifecycle -----------------------------------------------------------

    async def start(
        self,
        opening: SessionStart,
        *,
        binary: str,
        cwd: str,
        env: dict[str, str],
        args: list[str],
        target: dict | None,
        skills: dict[str, str] | None = None,
        extension: dict[str, str] | None = None,
        notice: str = "",
        tools: dict | None = None,
        subagent_rules: str = "",
    ) -> str:
        """Start pi in `cwd`, a directory of this host's that holds nothing of
        the project: everything the session does in the project is on the room's
        machine, as `target` names it (`machine.py`).

        A `target` of None is a session with no hands at all (`_start_without_
        hands`): a person's 芝士, which has no project, no machine and no files,
        and only the tools `tools` lists. A `target` with `tools` is one that
        only reads the room's machine besides them: a document's 芝士."""
        self.claim()
        # pi's own temporary files — the whole output of a long command, which
        # its bash names for the model to read — stay with the session.
        scratch = self.state / "tmp"
        scratch.mkdir(exist_ok=True)
        env = {**env, "TMPDIR": str(scratch)}
        self.cwd, self.env = cwd, env
        self.binary, self.args = binary, list(args)
        self.model = opening.model or ""
        self.extension_files = dict(extension or {})
        if target is None or tools is not None:
            if target is not None:
                self.machine = Machine(target, scratch=scratch, reading=True)
                self.workspace = self.machine.workspace
            return await self._start_without_hands(
                opening, binary, cwd, env, extension or {}, tools or {}
            )
        mirror = self.state / "project-skills"
        self.machine = Machine(
            target, shipped=self.state / "skills", mirror=mirror, scratch=scratch
        )
        self.workspace = self.machine.workspace
        session_id = self._session_id(opening)
        # The room's system prompt reaches pi as a FILE it is pointed at, never
        # as argv: it is assembled per room and runs to multiple KB, and argv is
        # both size-capped and readable by anyone who can list processes.
        prompt = self.state / "system-prompt.md"
        prompt.write_text(opening.system_prompt, encoding="utf-8")
        appended = (
            ["--append-system-prompt", str(prompt)] if opening.system_prompt else []
        )
        # pi starts with `--no-skills` because it would otherwise read whatever
        # this host keeps in its own ~/.agents. That drops the project's own
        # skills as well, so they are named here, found on the machine the way pi
        # finds them in a project it trusts and copied where pi can load them.
        # They come first: pi keeps the first skill of a name, and in a project
        # opened with plain pi, the project's skill wins over one of the same
        # name from anywhere else. A session started before its machine has
        # neither the project's skills nor what the repository says; it is
        # started again on the machine once it has one.
        shutil.rmtree(mirror, ignore_errors=True)
        if not self.machine.placeholder:
            try:
                self.context = await asyncio.to_thread(self.machine.context)
                for path in await asyncio.to_thread(self.machine.project_skills):
                    appended += ["--skill", path]
            except Exception as error:  # noqa: BLE001 — the room still opens
                print(f"[cheese] the project was not read: {error!r}", file=sys.stderr)
        # Explicit `--skill` paths are additive even under `--no-skills`, so the
        # platform's own skills are written here and named — for the same
        # reason the system prompt is: they are assembled by the platform, and
        # this host has no copy to point at.
        #
        # A skill is a directory and everything under it travels, so the files
        # go wherever their relative paths say — but only the directories that
        # hold a SKILL.md are named. Naming every file's parent would point pi
        # at `skills/documents/references`, which is not a skill at all.
        for relative, content in sorted((skills or {}).items()):
            file = self.state / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(content, encoding="utf-8")
            if file.name == "SKILL.md":
                appended += ["--skill", str(file.parent)]
        # A subagent works in the same project with the same ways of working.
        self.skill_args = [
            value
            for flag, path in zip(appended[::2], appended[1::2], strict=True)
            if flag == "--skill"
            for value in (flag, path)
        ]
        self.jobs = Jobs(self.machine, self.state / "bg")
        self.jobs.resume()
        if extension is not None:
            await self.open_servers()
            home = self.write_extension(extension, notice)
            appended += ["--extension", str(home / "index.ts")]
            # Named rather than derived: an extension that had to work out
            # where it was written would be guessing at a path the runner
            # already knows, and the first thing a wrong guess costs is every
            # platform tool in the room.
            env = {**env, "CHEESE_PI_EXTENSION": str(home)}
        self.notice = notice
        self.subagent_rules = subagent_rules
        return await self._run(binary, session_id, [*appended, *args], cwd, env)

    def _session_id(self, opening: SessionStart) -> str:
        """The session this state directory holds, chosen once (see the module
        docstring) and kept; the owner every record is stamped with."""
        saved = self.journal.recall("session_id")
        if saved is not None and opening.resume_token not in (None, saved):
            raise ValueError("A session directory cannot resume a different session")
        session_id = saved or opening.resume_token or str(uuid.uuid4())
        self.journal.remember("session_id", session_id)
        self.journal.remember(
            "owner",
            json.dumps({"harness": "pi", "agent_handle": opening.agent_handle}),
        )
        return session_id

    async def _start_without_hands(
        self,
        opening: SessionStart,
        binary: str,
        cwd: str,
        env: dict[str, str],
        extension: dict[str, str],
        tools: dict,
    ) -> str:
        """A session with nothing to work on but a conversation: no project, no
        files and no commands — pi's own tools are not even enabled — only the
        tools ``tools`` names, which the platform runs (``run_cli``). Its system
        prompt replaces pi's own, which is a coding assistant's and describes
        tools this session does not have.

        With a machine to read (`start`), pi's tools that only read are enabled
        as well, their hands on the room's checkout. Nothing else of the room's
        reaches it: the repository's instructions and skills, its MCP servers,
        background jobs and subagents are read or run by commands, and this
        session runs none."""
        self.tools = tools
        specs = catalog.schemas_of(list(tools.get("names") or []))
        session_id = self._session_id(opening)
        prompt = self.state / "system-prompt.md"
        prompt.write_text(opening.system_prompt, encoding="utf-8")
        own = READING_TOOLS if self.machine is not None else ()
        names = ",".join([*own, *(spec["name"] for spec in specs)])
        flags = ["--system-prompt", str(prompt)]
        flags += ["--tools", names] if names else ["--no-tools"]
        home = self.write_extension(extension, "", tools=specs)
        flags += ["--extension", str(home / "index.ts")]
        env = {**env, "CHEESE_PI_EXTENSION": str(home)}
        return await self._run(binary, session_id, [*flags, *self.args], cwd, env)

    async def _run(
        self,
        binary: str,
        session_id: str,
        flags: list[str],
        cwd: str,
        env: dict[str, str],
    ) -> str:
        self.errors = (self.state / "pi.log").open("ab")
        self.process = await asyncio.create_subprocess_exec(
            binary,
            "--mode",
            "rpc",
            "--session-id",
            session_id,
            "--session-dir",
            str(self.state / "sessions"),
            *flags,
            cwd=cwd,
            env=self.agent_env(env),
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
        self.refresher = asyncio.create_task(self._answer_the_door())
        # Whatever the session already holds is ours to land before the first
        # input: resuming a session means its history is already there. Nothing
        # in it is waiting on a verdict: whatever pi was doing ended with the
        # process that did it, so a failure in it is not held back.
        await self.refresh()
        self.holding = True
        await self.listen(LINE_LIMIT)
        return session_id

    # --- writing -------------------------------------------------------------

    async def send(
        self,
        identifier: str,
        text: str,
        *,
        steering: bool = False,
        images: list[dict] | None = None,
        work_id: str | None = None,
        owes_reply: bool = False,
    ) -> dict:
        """``prompt``, or ``steer`` for words said to a session mid-turn."""
        return await self.accept(
            identifier,
            {
                "text": text,
                "images": images or [],
                "work_id": work_id,
                "steer": steering,
            },
            lambda: self._submit(identifier, text, steering, images, work_id),
            owes_reply=owes_reply,
        )

    async def _submit(
        self,
        identifier: str,
        text: str,
        steering: bool,
        images: list[dict] | None,
        work_id: str | None,
    ) -> dict:
        assert self.client is not None
        if not steering:
            self.aborting = False
        if work_id is not None and not steering:
            # The input's own marker names it exactly (FB-56): which work a
            # later native user entry belongs to is read from this. Sending
            # is not consuming — the standing owner is NOT touched here, so
            # a still-unread predecessor's entries stay its own, and the
            # owner moves only when the native user entry actually lands
            # (`refresh`'s walk).
            nonce = nonce_in(text)
            if nonce:
                self.journal.remember(f"input:{nonce}", work_id)
        fields: dict = {"message": text}
        if images:
            fields["images"] = images
        if steering:
            await self.client.request("steer", **fields)
        else:
            await self.client.request("prompt", **fields)
        return {"input_id": identifier}

    async def _hold_to_reply(self, reason: str) -> None:
        """A turn of the session's own, to answer the person it left waiting.

        The room already saw the last one end — pi wrote its final message
        before it settled — so this is new work the room opens the books for
        when it speaks (`unsolicited`), as it does for a turn a finished
        background job starts."""
        assert self.client is not None
        owner = json.loads(self.journal.recall("owner") or "{}")
        owner.update(work_id=str(uuid.uuid4()), unsolicited=True)
        self.journal.remember("owner", json.dumps(owner))
        try:
            await self.client.request(
                "prompt", message=f"{self.notice}\n{reason}" if self.notice else reason
            )
        except Exception as error:  # noqa: BLE001 — the turn ended; so be it
            print(f"could not hold the turn to a reply: {error!r}", file=sys.stderr)
            self.reply_settled()

    async def yield_foreground(self) -> None:
        """Nothing to send: pi's shell runs inside pi, as the platform
        extension's `bash` (`platform.ts`), which watches the file the debt was
        just written to and lets go of its command as soon as it changes."""

    def busy(self) -> bool:
        return bool(
            self.working
            or (self.continuing is not None and not self.continuing.done())
            or any(not agent.ended.done() for agent in self.children.started.values())
            or (self.jobs is not None and self.jobs.running() > 0)
        )

    # --- the socket ----------------------------------------------------------

    async def dispatch(self, method: str, params: dict) -> dict:
        if method == "entries":
            await self.refresh()
            since = params.get("since")
            after = self.journal.sequence_of(since) if since else 0
            news = await self.news_for(after, params)
            return {"entries": [row["record"] for row in self.records(after)], **news}
        if method == "send":
            if "platform_token" in params:
                self.platform_token = str(params.get("platform_token") or "")
            return await self.send(
                params["input_id"],
                params["text"],
                images=params.get("images"),
                work_id=params.get("work_id"),
                owes_reply=bool(params.get("owes_reply")),
            )
        if method == "steer":
            return await self.send(
                params["input_id"],
                params["text"],
                steering=True,
                images=params.get("images"),
                work_id=params.get("work_id"),
                owes_reply=bool(params.get("owes_reply")),
            )
        if method == "cli":
            return await self.run_cli(
                params["tool"],
                params.get("arguments") or {},
                params.get("cwd"),
                params.get("id") or str(uuid.uuid4()),
            )
        if method == "mcp":
            if self.servers is None:
                raise ValueError("This session has no MCP servers")
            return await self.servers.call(
                params["tool"],
                params.get("arguments") or {},
                call_id=params.get("id") or str(uuid.uuid4()),
            )
        if method == "hooks":
            return await self.tool_hooks(params)
        if method == "files":
            return await self.files(params)
        if method == "git":
            assert self.machine is not None
            return await asyncio.to_thread(self.machine.git, params)
        if method == "shell":
            return await self.shell(params)
        if method == "context":
            assert self.machine is not None
            return {"context": self.context or self.machine.instructions}
        if method == "job_start":
            assert self.jobs is not None
            return await self.jobs.start(
                params["command"],
                params.get("label") or "",
                params.get("cwd") or self.workspace,
            )
        if method == "job_write":
            assert self.jobs is not None
            await self.jobs.write(params["id"], params["text"])
            return {}
        if method == "job_signal":
            assert self.jobs is not None
            await self.jobs.signal(params["id"], int(params["signal"]))
            return {}
        if method == "subagent_spawn":
            return await self.children.spawn(
                params["prompt"],
                description=params.get("description") or "",
                model=params.get("model"),
                background=bool(params.get("background")),
            )
        if method == "subagent_wait":
            return await self.children.wait(params["agent_id"])
        if method == "subagent_background":
            await self.children.background(params["agent_id"])
            return {}
        if method == "subagent_send":
            await self.children.send(params["agent_id"], params["message"])
            return {"sent": True}
        if method == "subagent_stop":
            return {"stopped": await self.children.stop(params["agent_id"])}
        if method == "abort":
            if self.client is None:
                return {"aborted": False}
            self.aborting = True
            # The work taken away includes what its subagents were doing — and
            # first: pi's abort waits for a `Task` call still waiting on one.
            await self.children.stop_all()
            await self.client.request("abort")
            self.working = False
            return {"aborted": True}
        if method == "ping":
            owner = json.loads(self.journal.recall("owner") or "{}")
            return {
                "pid": os.getpid(),
                "session_id": self.journal.recall("session_id"),
                "working": self.working,
                # Background jobs still running: a relaunch would end them.
                "tasks": self.jobs.running() if self.jobs is not None else 0,
                "work_id": owner.get("work_id"),
                "alive": self.alive(),
                "capabilities": list(self.capabilities),
                # How long since anything went in: which of a person's
                # sessions is the least recently used (`host.configure`).
                "idle_s": time.monotonic() - self.active_at,
                # The model it was started on: a session asked for another one
                # is started again (`session_host.SessionHost.start`).
                "model": self.model,
            }
        raise ValueError(f"Unknown pi session operation: {method}")

    async def stopped(self) -> None:
        with contextlib.suppress(Exception):
            await super().stopped()

    async def close(self) -> None:
        # A subagent lives and dies with the session that started it, and so do
        # the jobs it started: the room is going, and a dev server nobody can
        # reach any more would hold its port until somebody found it by hand.
        await self.children.stop_all()
        if self.jobs is not None:
            await self.jobs.close(end=True)
        if self.refresher is not None:
            self.refresher.cancel()
            await asyncio.gather(self.refresher, return_exceptions=True)
        await super().close()
