"""Own one headless Claude Code on the session machine, and outlive its readers.

Claude Code runs as ``claude -p`` with stream-json on both pipes, and this
process holds the pipes for the whole life of the session: a user message, a
message said mid-turn and a control are all a line written to its stdin, and
everything it reports is a line read from its stdout. Closing stdin ends the
session and kills whatever it had running in the background, so stdin is closed
exactly once, on purpose — when the session has been idle and unread for
``IDLE_EXIT_S``, or when the screen that supervises this runner goes away.

Every line read is recorded in the journal under a stable sequence, stamped with
the room's work it belongs to. The stamp is decided here because only this side
sees the order things happened in: the echo of an input we wrote
(``--replay-user-messages``) is what opens a turn for that input's work, a turn
that opens without one (a background task finishing woke the session) is a turn
the session started for itself, and ``result`` closes whichever is open.

One thing Claude Code does not put on stdout is read from disk into the same
journal: the transcripts of agents a subagent or a workflow starts, which only
their own ``subagents/**/agent-<id>.jsonl`` file holds.

Standard library only: this module travels in the runner archive.
"""

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.harness.claude_code.journal import Journal
from app.domain.agent.harness.driven import runner

# One stdout line can carry a whole tool result (Claude Code caps those at about
# 30,000 characters) or an image the session was handed. A line over the limit
# kills the reader, so the limit is a ceiling nothing legitimate reaches.
LINE_LIMIT = 64 * 1024 * 1024
# Idle with nothing running and nobody asking for the journal: the backend that
# read this session is gone, and a session nobody reads is one no room is using.
IDLE_EXIT_S = 600.0
CONTROL_TIMEOUT_S = 30.0
COMMAND_TIMEOUT_S = 120.0
TAIL_POLL_S = 0.5
# How often the runner checks whether it has been idle long enough to let go.
IDLE_CHECK_S = 5.0
# How long the runner keeps what it recorded, and how often it looks.
RETENTION_S = 7 * 24 * 3600
RETENTION_EVERY_S = 3600
# A tool result read from a transcript file is not capped the way stdout is.
# The room reads the tail of a failure and a subagent's conclusion; neither is
# worth more than stdout would have carried.
FILE_TEXT_MAX = 30_000
FINISHED = frozenset({"completed", "failed", "killed", "stopped"})
# How much of what Claude Code said on a failed way up goes into the runner's
# own log. The backend reads that log's last 1200 bytes (``channel._why``), and
# the end of what a dying process printed is where its reason is.
LAST_WORDS = 1000


def _is_claude(argv0: str) -> bool:
    """Whether a process's first argument is a Claude Code binary.

    A real executable is `<...>/claude`, `<...>/claude/versions/<v>` (the pin) or
    `<...>/.local/bin/claude`; `/.claude/` is the config directory and is not a
    match, which keeps the remote-execution helpers stored under it out.
    """
    return (
        argv0 == "claude" or "/claude/versions/" in argv0 or argv0.endswith("/claude")
    )


def sessions_on(config_dir: Path) -> list[int]:
    """Claude Code processes already using this config directory.

    Two sessions appending to one transcript corrupt it, and a runner that
    starts while an older session (a terminal of the harness this replaced, a
    runner that lost its lock) is still running would be the second.
    """
    marker = f"CLAUDE_CONFIG_DIR={config_dir}"
    found = []
    proc = Path("/proc")
    if proc.is_dir():
        for entry in proc.iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                argv0 = (entry / "cmdline").read_bytes().split(b"\0")[0].decode()
                environ = (entry / "environ").read_bytes().split(b"\0")
            except (OSError, UnicodeDecodeError):
                continue
            if _is_claude(argv0) and marker.encode() in environ:
                found.append(int(entry.name))
        return found
    try:
        listing = subprocess.run(
            ["ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return found
    for line in listing.splitlines():
        pid, _, command = line.strip().partition(" ")
        if not pid.isdigit() or not _is_claude(command.strip()):
            continue
        try:
            detail = subprocess.run(
                ["ps", "eww", "-p", pid, "-o", "command="],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        if marker in detail.split():
            found.append(int(pid))
    return found


def end(pids: list[int]) -> None:
    for number, wait in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 1.0)):
        for pid in pids:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, number)
        deadline = time.monotonic() + wait
        while pids and time.monotonic() < deadline:
            time.sleep(0.1)
            pids = [pid for pid in pids if _alive(pid)]
        if not pids:
            return


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def transcript(config_dir: Path, session_id: str) -> Path | None:
    """Where this session's transcript is, whatever cwd wrote it."""
    for path in config_dir.glob(f"projects/*/{session_id}.jsonl"):
        if path.is_file() and path.stat().st_size:
            return path
    return None


def _trim(content: object) -> object:
    """A tool result's text, cut to what stdout would have carried."""
    if isinstance(content, str):
        return content[-FILE_TEXT_MAX:]
    if isinstance(content, list):
        return [
            {**part, "text": part["text"][-FILE_TEXT_MAX:]}
            if isinstance(part, dict) and isinstance(part.get("text"), str)
            else part
            for part in content
        ]
    return content


def _without_pixels(content: object) -> object:
    """Content with the bytes of every image taken out.

    An image rides in the echo of the message that carried it, and in the
    result of a Read that opened one, as base64 — megabytes a page of the
    journal would carry to the backend for a room that shows neither.
    """
    if not isinstance(content, list):
        return content
    kept = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "image":
            block = {**block, "source": {"type": "omitted"}}
        elif isinstance(block, dict) and block.get("type") == "tool_result":
            block = {**block, "content": _without_pixels(block.get("content"))}
        kept.append(block)
    return kept


def lighter(record: dict) -> dict:
    message = record.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), list):
        return record
    return {
        **record,
        "message": {**message, "content": _without_pixels(message["content"])},
    }


def file_entry(entry: dict) -> dict | None:
    """The part of a transcript line the journal keeps, or None to skip it.

    Only what a person said and what the agent did: a transcript also holds the
    attachments the build feeds its own model (skill listings, the environment),
    and those are the bulk of a file and none of the room's business.
    """
    if entry.get("type") not in ("user", "assistant"):
        return None
    message = entry.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = [
            {**block, "content": _trim(block.get("content"))}
            if isinstance(block, dict) and block.get("type") == "tool_result"
            else block
            for block in content
        ]
    kept = {
        key: entry[key]
        for key in ("type", "uuid", "timestamp", "agentId", "isSidechain")
        if key in entry
    }
    return lighter({**kept, "message": {**message, "content": content}})


class Runner(runner.Runner[Journal]):
    def __init__(
        self,
        state: Path,
        *,
        idle_exit_s: float = IDLE_EXIT_S,
    ):
        super().__init__(state, Journal, "records.sqlite")
        self.idle_exit_s = idle_exit_s
        self.write_lock = asyncio.Lock()
        self.controls: dict[str, asyncio.Future] = {}
        self.commands: dict[str, asyncio.Future] = {}
        # Inputs written and not yet echoed: uuid → (kind, work id).
        self.sent: dict[str, tuple[str, str | None]] = {}
        self.working = False
        self.work: str | None = None
        self.unsolicited = False
        self.last_work: str | None = None
        self.interrupting = False
        # Harness tasks still running, by id → task type.
        self.tasks: dict[str, str] = {}
        # Tool calls made on the main thread; an agent started by one of them
        # reports on stdout, any other only in its own file.
        self.main_calls: set[str] = set()
        self.tailing: dict[str, str] = {}
        self.read_at = time.monotonic()
        self.session_id: str | None = None
        self.config_dir: Path | None = None
        self.helpers: list[asyncio.Task] = []
        self.proven = False

    # --- lifecycle -----------------------------------------------------------

    async def start(
        self,
        *,
        command: str,
        env: dict[str, str],
        resume: str | None,
        agent_handle: str | None,
    ) -> str:
        self.claim()
        self.config_dir = Path(env["CLAUDE_CONFIG_DIR"])
        end(sessions_on(self.config_dir))
        saved = self.journal.recall("session_id")
        failed = self.journal.recall("resume_failed")
        resumed = next(
            (
                candidate
                for candidate in (resume, saved)
                if candidate
                and candidate != failed
                and transcript(self.config_dir, candidate) is not None
            ),
            None,
        )
        if resumed is not None:
            self.session_id, flag = resumed, f"--resume {resumed}"
            # Consumed at the first proof the session is alive, or at a stop we
            # asked for; still here after an exit of its own, it names a
            # transcript this build cannot continue, and the next start begins
            # afresh instead of dying on it again.
            self.journal.remember("resuming", resumed)
        else:
            fresh = (
                saved if saved and transcript(self.config_dir, saved) is None else None
            )
            self.session_id = fresh or str(uuid.uuid4())
            flag = f"--session-id {self.session_id}"
        self.journal.remember("session_id", self.session_id)
        self.journal.remember(
            "owner",
            json.dumps({"harness": CLAUDE_CODE, "agent_handle": agent_handle}),
        )
        self.last_work = self.journal.recall("last_work")
        self.errors = (self.state / "claude.log").open("ab")
        # Earlier starts appended here too; this start's words begin here.
        self.errors_from = self.errors.tell()
        self.process = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            f"exec {command} {flag}",
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self.errors,
            limit=LINE_LIMIT,
            start_new_session=True,
        )
        self.listener = asyncio.create_task(self._read())
        self.helpers = [
            asyncio.create_task(self._tail()),
            asyncio.create_task(self._watch()),
        ]
        await self.listen(LINE_LIMIT)
        return self.session_id

    async def stopped(self) -> None:
        with contextlib.suppress(Exception):
            await super().stopped()

    async def close(self) -> None:
        for helper in self.helpers:
            helper.cancel()
        await asyncio.gather(*self.helpers, return_exceptions=True)
        # Claude Code that exited by itself, failing, before it ever answered
        # was handed a transcript it cannot continue. An exit we asked for, or
        # a clean one, says nothing about the transcript.
        process = self.process
        failed = (
            process is not None
            and process.returncode not in (None, 0)
            and not self.proven
        )
        with contextlib.suppress(Exception):
            resuming = self.journal.recall("resuming")
            if failed and resuming:
                self.journal.remember("resume_failed", resuming)
            self.journal.remember("resuming", "")
        if failed and process is not None:
            self._last_words(process.returncode)
        await super().close()

    def _last_words(self, status: int | None) -> None:
        """Say why Claude Code never came up, where the backend will look.

        Its socket goes with this runner, so a backend still waiting for the
        session can only read the runner's own log (``channel._why``). The reason
        is in Claude Code's stderr — the executor client's ``bootstrap`` runs in
        front of the binary and fails there, before any model is asked — and
        without this the room was told only that the socket was missing.
        """
        with contextlib.suppress(OSError):
            with (self.state / "claude.log").open("rb") as log:
                end = log.seek(0, os.SEEK_END)
                log.seek(max(self.errors_from, end - LAST_WORDS))
                said = log.read().decode("utf-8", "replace").strip()
            print(
                f"Claude Code exited with status {status} before it started"
                + (f":\n{said}" if said else ", and said nothing"),
                file=sys.stderr,
                flush=True,
            )

    # --- stdout --------------------------------------------------------------

    async def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        while line := await self.process.stdout.readline():
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except ValueError:
                # Something on the launch path printed to stdout. One line lost
                # is better than a session that stops being read.
                continue
            if not isinstance(record, dict):
                continue
            kind = record.get("type")
            if kind == "control_response":
                response = record.get("response") or {}
                future = self.controls.get(str(response.get("request_id")))
                if future is not None and not future.done():
                    future.set_result(response)
                continue
            if kind == "control_request":
                # Nothing asks the driver anything under its flags: permissions
                # are bypassed and the question tool is not offered. A request
                # left unanswered would hold the turn for ever, so it is refused.
                await self._write(
                    {
                        "type": "control_response",
                        "response": {
                            "subtype": "error",
                            "request_id": record.get("request_id"),
                            "error": "This session takes no requests from its driver",
                        },
                    }
                )
                continue
            if kind == "keep_alive":
                continue
            self.observe(record)

    def observe(self, record: dict, *, from_file: bool = False) -> None:
        """Stamp one record with the work it belongs to, and journal it."""
        kind = record.get("type")
        main = not from_file and record.get("parent_tool_use_id") is None
        stamp: dict = {}
        if kind == "system" and record.get("subtype") == "init":
            self.proven = True
        if kind == "command_lifecycle" and record.get("state") == "started":
            # The build takes an input: the one moment that says which input a
            # turn is for. A message said mid-turn is taken at a tool boundary
            # inside the turn it was said to; one the session reads only after
            # that turn ended starts a turn of its own.
            if not self.working:
                self._open(self.sent.get(str(record.get("command_uuid"))))
                stamp["turn_start"] = True
        elif kind == "user" and record.get("isReplay"):
            sent = self.sent.pop(str(record.get("uuid")), None)
            if sent is not None and sent[0] in ("send", "steer"):
                stamp["receipt"] = True
            if not self.working:
                self._open(sent)
                stamp["turn_start"] = True
        elif main and not self.working and kind in ("assistant", "user"):
            # Nothing of ours started this. A background task finished and the
            # notification woke the session.
            self._open(None)
            stamp["turn_start"] = True
        if main and kind == "assistant":
            for block in (record.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    self.main_calls.add(str(block.get("id")))
        if kind == "system":
            self._track(record)
        work = self.work if self.working else self.last_work
        if kind == "result" and self.interrupting:
            stamp["interrupted"] = True
        owner = json.loads(self.journal.recall("owner") or "{}")
        if work is not None:
            owner["work_id"] = work
            if self.working and self.unsolicited:
                owner["unsolicited"] = True
        self.journal.append({**lighter(record), "cheese": {**owner, **stamp}})
        if kind == "result":
            waiting = {str(record.get("user_message_uuid"))} | {
                str(identifier) for identifier in record.get("user_message_uuids") or []
            }
            for identifier in waiting:
                future = self.commands.pop(identifier, None)
                if future is not None and not future.done():
                    future.set_result(record)
            self._end()

    def _open(self, sent: tuple[str, str | None] | None) -> None:
        """A turn begins, for the input that started it (None: no input of ours)."""
        how, work = sent or ("", None)
        if how == "send":
            self.working, self.work, self.unsolicited = True, work, False
        elif how == "internal":
            # A platform command (`/reload-plugins`): no room turn.
            self.working, self.work, self.unsolicited = True, None, False
        else:
            # The session's own turn: a background task woke it, a notice the
            # runner wrote did, or a message it read only after the turn it was
            # said to had ended.
            self.working, self.work, self.unsolicited = True, str(uuid.uuid4()), True

    def _end(self) -> None:
        if self.work is not None:
            self.last_work = self.work
            self.journal.remember("last_work", self.work)
        self.working, self.work, self.unsolicited = False, None, False
        self.interrupting = False

    def _track(self, record: dict) -> None:
        """What is running, and which agents only their own file reports on."""
        task = str(record.get("task_id") or "")
        subtype = record.get("subtype")
        if subtype == "task_started" and task:
            self.tasks[task] = str(record.get("task_type") or "")
            if record.get("task_type") == "local_agent":
                if str(record.get("tool_use_id")) not in self.main_calls:
                    self.tailing.setdefault(task, "agent")
        elif subtype == "task_progress":
            for entry in record.get("workflow_progress") or []:
                if isinstance(entry, dict) and entry.get("agentId"):
                    self.tailing.setdefault(str(entry["agentId"]), "workflow")
        elif subtype == "task_notification" and task:
            self.tasks.pop(task, None)
        elif subtype == "task_updated" and task:
            status = (record.get("patch") or {}).get("status")
            if status in FINISHED:
                self.tasks.pop(task, None)

    # --- the two things only the disk knows ----------------------------------

    def _files(self) -> list[tuple[str, Path]]:
        assert self.config_dir is not None
        found = []
        roots = list(self.config_dir.glob(f"projects/*/{self.session_id}/subagents"))
        for agent, where in self.tailing.items():
            pattern = (
                f"agent-{agent}.jsonl"
                if where == "agent"
                else f"workflows/wf_*/agent-{agent}.jsonl"
            )
            for root in roots:
                found.extend((agent, path) for path in root.glob(pattern))
        return found

    async def _tail(self) -> None:
        while True:
            await asyncio.sleep(TAIL_POLL_S)
            for agent, path in self._files():
                key = f"tail:{path}"
                offset = int(self.journal.recall(key) or 0)
                try:
                    with path.open("rb") as source:
                        source.seek(offset)
                        chunk = source.read()
                except OSError:
                    continue
                # Only whole lines: the build may be halfway through writing one.
                complete = chunk[: chunk.rfind(b"\n") + 1]
                if not complete:
                    continue
                for line in complete.splitlines():
                    try:
                        entry = file_entry(json.loads(line))
                    except ValueError:
                        continue
                    if entry is not None:
                        self.observe(
                            {"type": "cheese_file", "agent_id": agent, "entry": entry},
                            from_file=True,
                        )
                self.journal.remember(key, str(offset + len(complete)))

    async def _watch(self) -> None:
        expired_at = 0.0
        while True:
            if time.monotonic() - expired_at >= RETENTION_EVERY_S:
                expired_at = time.monotonic()
                self.journal.expire(
                    (datetime.now(UTC) - timedelta(seconds=RETENTION_S)).isoformat()
                )
            await asyncio.sleep(IDLE_CHECK_S)
            if (
                self.idle_exit_s
                and not self.working
                and not self.tasks
                and not self.sent
                and not self.controls
                and not self.commands
                and not self.inputs
                and time.monotonic() - self.read_at >= self.idle_exit_s
            ):
                await self.release()
                return

    async def release(self) -> None:
        """Let the session go the way it is meant to: close its stdin."""
        assert self.process is not None and self.process.stdin is not None
        with contextlib.suppress(ConnectionError, BrokenPipeError):
            self.process.stdin.close()
            await self.process.stdin.wait_closed()

    # --- stdin ---------------------------------------------------------------

    async def _write(self, message: dict) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("The session is not running")
        async with self.write_lock:
            self.process.stdin.write(
                json.dumps(message, ensure_ascii=False).encode() + b"\n"
            )
            await self.process.stdin.drain()

    async def _put(
        self,
        identifier: str,
        work: str | None,
        text: str,
        images: list[dict],
        how: str,
    ) -> None:
        content: str | list = (
            [{"type": "text", "text": text}, *images] if images else text
        )
        self.sent[identifier] = (how, work)
        try:
            await self._write(
                {
                    "type": "user",
                    "uuid": identifier,
                    "message": {"role": "user", "content": content},
                    "parent_tool_use_id": None,
                    "session_id": "",
                }
            )
        except BaseException:
            self.sent.pop(identifier, None)
            raise

    async def send(
        self,
        identifier: str,
        text: str,
        *,
        images: list[dict] | None = None,
        work_id: str | None = None,
        steering: bool = False,
    ) -> dict:
        """A user message, or words said to a session that is working.

        Both are the same line on stdin: the build takes a message written
        mid-turn at the next tool boundary. They differ only in whose turn a
        message opens when the session happens to be idle by the time it reads
        it — ``steering`` says it was addressed to a turn already running.
        """
        how = "steer" if steering else "send"

        async def submit() -> dict:
            await self._put(identifier, work_id, text, images or [], how)
            return {"input_id": identifier}

        return await self.accept(
            identifier,
            {"text": text, "images": images or [], "work_id": work_id, "how": how},
            submit,
        )

    async def control(self, request: dict, timeout: float = CONTROL_TIMEOUT_S) -> dict:
        identifier = f"cheese-{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        self.controls[identifier] = future
        try:
            await self._write(
                {
                    "type": "control_request",
                    "request_id": identifier,
                    "request": request,
                }
            )
            return await asyncio.wait_for(future, timeout)
        finally:
            self.controls.pop(identifier, None)

    async def command(self, text: str, timeout: float = COMMAND_TIMEOUT_S) -> dict:
        """A slash command the platform runs (`/reload-plugins`), to its result."""
        identifier = f"cheese-command-{uuid.uuid4()}"
        future = asyncio.get_running_loop().create_future()
        self.commands[identifier] = future
        try:
            await self._put(identifier, None, text, [], "internal")
            record = await asyncio.wait_for(future, timeout)
        finally:
            self.commands.pop(identifier, None)
        return {
            "result": record.get("result"),
            "is_error": bool(record.get("is_error")),
        }

    # --- the socket ----------------------------------------------------------

    async def dispatch(self, method: str, params: dict) -> dict:
        self.read_at = time.monotonic()
        if method == "events":
            return {"events": self.journal.read(int(params.get("after", 0)))}
        if method in ("send", "steer"):
            return await self.send(
                params["input_id"],
                params["text"],
                images=params.get("images"),
                work_id=params.get("work_id"),
                steering=method == "steer",
            )
        if method == "interrupt":
            self.interrupting = self.working
            answer = await self.control({"subtype": "interrupt"})
            return {"interrupted": answer.get("subtype") == "success"}
        if method == "control":
            return await self.control(params["request"])
        if method == "command":
            return await self.command(params["text"])
        if method == "ping":
            return {
                "pid": os.getpid(),
                "session_id": self.session_id,
                "working": self.working,
                "work_id": self.work if self.working else None,
                "tasks": dict(self.tasks),
                "alive": self.process is not None and self.process.returncode is None,
            }
        raise ValueError(f"Unknown Claude Code session operation: {method}")
