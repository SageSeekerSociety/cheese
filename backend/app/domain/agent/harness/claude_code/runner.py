"""Own one headless Claude Code on the session machine, and outlive its readers.

Claude Code runs as ``claude -p`` with stream-json on both pipes, and this
process holds the pipes for the whole life of the session: a user message, a
message said mid-turn and a control are all a line written to its stdin, and
everything it reports is a line read from its stdout. Closing stdin ends the
session and kills whatever it had running in the background, so stdin is closed
exactly once, on purpose — when the session has sat idle (``driven.runner``),
or when the screen that supervises this runner goes away.

Every line read is recorded in the journal under a stable sequence, stamped with
the room's work it belongs to — except the deltas of the block being written,
which are only shown while it is written (``stream``). The stamp is decided
here because only this side sees the order things happened in: the echo of an
input we wrote (``--replay-user-messages``) is what opens a turn for that
input's work, a turn that opens without one (a background task finishing woke
the session) is a turn the session started for itself, and ``result`` closes
whichever is open.

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
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.harness.claude_code.journal import Journal
from app.domain.agent.harness.claude_code.protocol import INPUT_PROTOCOL
from app.domain.agent.harness.driven import runner
from app.domain.memory.files import (
    INDEX_NAME,
    MEMORY_ROOT,
    PROJECT_PREFIX,
    check_scoped_path,
    rejected_path,
)
from app.domain.memory.tree import (
    BULK_DELETE_MIN,
    BULK_DELETE_RATIO,
    prefixes_of,
    sync_tree,
)

# One stdout line can carry a whole tool result (Claude Code caps those at about
# 30,000 characters) or an image the session was handed. A line over the limit
# kills the reader, so the limit is a ceiling nothing legitimate reaches.
LINE_LIMIT = 64 * 1024 * 1024
CONTROL_TIMEOUT_S = 30.0
COMMAND_TIMEOUT_S = 120.0
# How long a new turn waits to see the project's context as it is now before it
# starts on what the session already has.
CATCH_UP_TIMEOUT_S = 30.0
TAIL_POLL_S = 0.5
# How long the runner keeps what it recorded, and how often it looks.
RETENTION_S = 7 * 24 * 3600
RETENTION_EVERY_S = 3600
# A tool result read from a transcript file is not capped the way stdout is.
# The room reads the tail of a failure and a subagent's conclusion; neither is
# worth more than stdout would have carried.
FILE_TEXT_MAX = 30_000
FINISHED = frozenset({"completed", "failed", "killed", "stopped"})
# How much of what Claude Code said on a failed way up goes into the runner's
# own log. The backend reads the last 1200 bytes of this launch's part of that
# log (``channel._ended``), and the end of what a dying process printed is where
# its reason is.
LAST_WORDS = 1000
# The platform names each launch in this variable. The runner's log is appended
# across launches, so every record of a runner ending before its session came
# up opens with ``ended(launch)``, and a backend waiting on one launch reads
# only what that launch left.
LAUNCH = "CHEESE_RUNNER_LAUNCH"
# Where the session's own helpers reach this runner. The same name as
# `executor_transport.SESSION_SOCKET`, which reads it: that file ships to the
# session host on its own and cannot import this one.
SESSION_SOCKET = "CHEESE_SESSION_SOCKET"


def ended(launch: str) -> str:
    return f"cheese-runner {launch} ended"


def _is_claude(argv0: str) -> bool:
    """Whether a process's first argument is a Claude Code binary.

    A real executable is `<...>/claude`, `<...>/claude/versions/<v>` (the pin) or
    `<...>/.local/bin/claude`; `/.claude/` is the config directory and is not a
    match, which keeps the remote-execution helpers stored under it out.
    """
    return (
        argv0 == "claude" or "/claude/versions/" in argv0 or argv0.endswith("/claude")
    )


def sessions_on(config_dir: Path, agent_handle: str | None) -> list[int]:
    """Claude Code processes already using this seat and config directory.

    A new runner replaces an older process for its own seat. Teammates in one
    room share the config directory, so that directory alone is not ownership.
    """
    if not agent_handle:
        return []
    marker = f"CLAUDE_CONFIG_DIR={config_dir}"
    owner = f"CHEESE_AUTHOR={agent_handle}"
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
            if (
                _is_claude(argv0)
                and marker.encode() in environ
                and owner.encode() in environ
            ):
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
        if marker in detail.split() and owner in detail.split():
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


#: 上一次对账写下去的那一版（路径 → 指纹），**和那棵树放在同一个目录里**。
#:
#: 它是三方合并的中间那一方：没有它，「两边都动了」就没有第三个答案可以问。它跟
#: 着树走，因为「上次铺下去的是什么」只有和「铺下去的那些文件」在一起才是真的。
#: 放在别处（journal 里）出过一次真的会丢记忆的事：会话的家被重建（`resource_cleanup`
#: 会把它整个删掉），树没了而那张表活了下来，于是每一条平台上的记忆都看起来像是
#: 会话刚刚删掉的，一次对账就把 project 和发言人的 private 整棵删光——删除没有历史
#: 可以恢复。放在这里，家一没这张表跟着没，下一次对账就是一次全新的铺。
MEMORY_BASELINE = ".baseline.json"

#: 记忆作用域改名前的那一层目录：`team/` → `project/`（见 `files.PROJECT_PREFIX`）。
#: 会话机上可能还留着 `~/.cheese/memory/team/` 和一份以 `team/…` 为键的基线，改名
#: 那一轮必须先把它们搬到 `project/` 那一边（`_migrate_legacy_scope`）。
LEGACY_SCOPE_DIR = "team"


def memory_root() -> Path:
    """会话里那棵记忆树的根。

    用的是会话自己的 `$HOME`（`machine_launcher` 把它换成了这一间房的会话家），
    不是机器主人的家：一个会话一个家，记忆的副本也就一间房一份。
    """
    home = os.environ.get("HOME") or ""
    if not home:
        raise RuntimeError("The session has no home directory to keep memory in")
    return Path(home) / MEMORY_ROOT


def memory_scopes(params: dict) -> dict[str, dict[str, str]]:
    """请求里的那一份树，逐条验过再收下。

    这是信任边界：请求说的路径会被拿去当文件路径写，所以「写哪儿」由这里说了
    算，不由请求说了算。作用域前缀、文件名、正文各有各的规矩，一条不合就整次拒
    掉——半个树铺下去比一次都没铺更糟，agent 会照着半个树做事。
    """
    scopes = params.get("scopes")
    if not isinstance(scopes, dict):
        raise ValueError("Memory sync needs a scopes object")
    out: dict[str, dict[str, str]] = {}
    for prefix, files in scopes.items():
        if not isinstance(prefix, str) or not isinstance(files, dict):
            raise ValueError("Memory scopes are prefix → files")
        for name, content in files.items():
            if not isinstance(name, str) or not isinstance(content, str):
                raise ValueError("A memory file is a name and its text")
            check_scoped_path(f"{prefix}/{name}")
        out[prefix] = files
    return out


def _recall_baseline(root: Path) -> dict[str, str]:
    """上一次对账留下的指纹表（就在记忆树根上，见 `MEMORY_BASELINE`）。

    读不出来当没写过——一次对账从头铺一遍，比拿着一张读不懂的表去判「谁改过」安
    全。读不到也正是「这棵树是新的」：家被重建过，于是每一条都按平台的版本铺。
    """
    try:
        baseline = json.loads((root / MEMORY_BASELINE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(baseline, dict):
        return {}
    return {
        str(path): str(fingerprint)
        for path, fingerprint in baseline.items()
        if isinstance(path, str) and isinstance(fingerprint, str)
    }


def _write_memory(root: Path, path: str, content: str) -> None:
    """原子写入：先写同目录的临时文件，再改名。

    claude 和这个 runner 是并发的，它可能正在读这个文件；读到一个写了一半的记忆
    比读到一个旧版本糟得多——旧版本至少是一条真写过的记忆。
    """
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent, delete=False
    )
    try:
        with handle:
            handle.write(content)
        os.replace(handle.name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(handle.name)
        raise


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
        idle_exit_s: float = runner.IDLE_EXIT_S,
        launch: str = "",
    ):
        super().__init__(state, Journal, "records.sqlite", idle_exit_s=idle_exit_s)
        self.launch = launch
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
        self.session_id: str | None = None
        self.config_dir: Path | None = None
        # Where the session's execution target is, when it runs against an
        # executor: the context `_catch_up` synchronizes before a turn.
        self.execution: str | None = None
        self.helpers: list[asyncio.Task] = []
        self.proven = False
        # The index of the content block being shown (``stream``).
        self.streaming: int | None = None

    capabilities = (runner.LONG_POLL, runner.LIVE)

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
        self.execution = env.get("CHEESE_EXECUTION_CONFIG")
        end(sessions_on(self.config_dir, agent_handle))
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
            # The session's own helpers reach this runner here, to change the
            # session while it runs (`executor_transport.register_project_hooks`).
            env=self.agent_env({**env, SESSION_SOCKET: runner.socket_path(self.state)}),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self.errors,
            limit=LINE_LIMIT,
            start_new_session=True,
        )
        self.listener = asyncio.create_task(self._read())
        self.helpers = [
            asyncio.create_task(self._tail()),
            asyncio.create_task(self._expire()),
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
        session can only read the runner's own log (``channel._ended``). The reason
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
                f"{ended(self.launch)}: "
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
            if kind == "system" and record.get("subtype") == "thinking_tokens":
                # The build's running estimate of how much it has thought, one
                # line per streamed delta of a token or two. Nothing reads it,
                # and journaled it was nineteen records in twenty.
                continue
            if kind == "stream_event":
                # What the build is in the middle of writing
                # (`--include-partial-messages`): shown, never journaled.
                self.stream(record)
                continue
            self.observe(record)

    def stream(self, record: dict) -> None:
        """Show the block the session is writing as its deltas arrive.

        The build writes each finished block as an ``assistant`` record of its
        own before it says the block stopped, so that record is what replaces
        what is shown (``observe``). A subagent's writing is its own, and
        reasoning is not shown: a thinking block clears what was.
        """
        if record.get("parent_tool_use_id") is not None:
            return
        event = record.get("event") or {}
        kind = event.get("type")
        if kind == "content_block_start":
            block = event.get("content_block") or {}
            self.streaming = event.get("index")
            work = self.work if self.working else None
            if block.get("type") == "text":
                self.show([{"type": "text", "text": block.get("text") or ""}], work)
            elif block.get("type") == "tool_use":
                self.show(
                    [
                        {
                            "type": "tool",
                            "id": block.get("id"),
                            "name": block.get("name"),
                            "arguments": "",
                        }
                    ],
                    work,
                )
            else:
                self.show([], None)
        elif kind == "content_block_delta" and self.live_blocks:
            if event.get("index") != self.streaming:
                return
            delta, block = event.get("delta") or {}, self.live_blocks[-1]
            if delta.get("type") == "text_delta" and block["type"] == "text":
                block["text"] += str(delta.get("text") or "")
                self.shown()
            elif delta.get("type") == "input_json_delta" and block["type"] == "tool":
                block["arguments"] += str(delta.get("partial_json") or "")
                self.shown()

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
                self._open(self._sent_input(str(record.get("command_uuid"))))
                stamp["turn_start"] = True
        elif main and kind == "user" and record.get("isReplay"):
            identifier = str(record.get("uuid"))
            sent = self._sent_input(identifier)
            self.sent.pop(identifier, None)
            if not self.working:
                self._open(sent)
                stamp["turn_start"] = True
            receipt = self.journal.recall(f"receipt:{identifier}")
            if receipt is not None:
                identity = json.loads(receipt)
                if identity["session_id"] == self.session_id:
                    stamp.update(
                        receipt=True,
                        receipt_work_id=identity["work_id"],
                        receipt_session_id=identity["session_id"],
                        receipt_execution_work_id=self.work,
                    )
                    key = f"execution_inputs:{self.work}"
                    inputs = set(json.loads(self.journal.recall(key) or "[]"))
                    inputs.add(identifier)
                    self.journal.remember(key, json.dumps(sorted(inputs)))
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
        elif (
            main
            and kind == "result"
            and self.working
            and self.work is not None
            and not record.get("is_error")
        ):
            inputs = json.loads(
                self.journal.recall(f"execution_inputs:{self.work}") or "[]"
            )
            if inputs:
                stamp["work_completed"] = True
                stamp["completion_session_id"] = self.session_id
                stamp["completion_input_ids"] = inputs
        if (
            main
            and kind == "result"
            and (self.interrupting or record.get("is_error"))
            and self.working
            and self.work is not None
            and self.session_id is not None
        ):
            # An error or a Stop ends this work without ever reaching the
            # completion stamp, and an input row left unfinished blocks the
            # seat for good. Say so when the exact work identity holds and the
            # interval of inputs it really owned is known — the same journal
            # key the completion stamp reads — and say it on this record,
            # because this is the last one this work can ever be written on:
            # the next belongs to a new work. A withheld termination is not a
            # delay, it is a seat that never opens again. A harness background
            # task still running is no reason to withhold it: that task goes on
            # in an interval of its own, which is what the completion above
            # assumes too. What the stamp opens is the seat, not the work: the
            # rows keep their holds, and a later clean completion still writes
            # their completed_at.
            inputs = json.loads(
                self.journal.recall(f"execution_inputs:{self.work}") or "[]"
            )
            if inputs:
                stamp["work_terminated"] = True
                stamp["termination"] = (
                    "interrupted" if self.interrupting else "is_error"
                )
                stamp["termination_session_id"] = self.session_id
                stamp["termination_work_id"] = self.work
                stamp["termination_input_ids"] = inputs
        owner = json.loads(self.journal.recall("owner") or "{}")
        if work is not None:
            owner["work_id"] = work
            if self.working and self.unsolicited:
                owner["unsolicited"] = True
        self.journal.append({**lighter(record), "cheese": {**owner, **stamp}})
        if main and kind in ("assistant", "result"):
            # The block being shown has landed, or the turn ended without it.
            self.show([], None)
        if kind == "result":
            waiting = {str(record.get("user_message_uuid"))} | {
                str(identifier) for identifier in record.get("user_message_uuids") or []
            }
            for identifier in waiting:
                future = self.commands.pop(identifier, None)
                if future is not None and not future.done():
                    future.set_result(record)
            self._end()

    def _sent_input(self, identifier: str) -> tuple[str, str | None] | None:
        sent = self.sent.get(identifier)
        if sent is not None:
            return sent
        receipt = self.journal.recall(f"receipt:{identifier}")
        if receipt is None:
            return None
        identity = json.loads(receipt)
        if identity["session_id"] != self.session_id:
            return None
        return identity["how"], identity["work_id"]

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
        # A person's message the build has not taken yet opens the next turn,
        # and it is that turn which owes the answer.
        if self.owed not in self.sent:
            self.reply_settled()

    def _track(self, record: dict) -> None:
        """What is running, and which agents only their own file reports on."""
        task = str(record.get("task_id") or "")
        subtype = record.get("subtype")
        if subtype == "task_started" and task:
            self.tasks[task] = str(record.get("task_type") or "")
            # A command that was only starting when a person's message came in
            # was not the build's to move yet, and the message would wait for
            # it: move it too, as long as the message is still unread.
            if self.owed is not None and self.owed in self.sent:
                self.helpers.append(asyncio.create_task(self._yield_again()))
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

    # --- the three things only the disk knows --------------------------------

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

    # --- memory: the tree the agent and the platform both write --------------

    @staticmethod
    def _migrate_legacy_scope(root: Path) -> None:
        """把改名前的 `team/` 目录和基线搬到 `project/` 那一边（一次，幂等）。

        作用域从 team 改叫 project 之后，平台铺下来的是 `project/<name>.md`，而会话
        机上还躺着一棵 `team/`。不搬会出两件事，两件都当真会丢记忆：

        - 三方合并认不出这两条是同一份：磁盘上只有 `team/a.md`、平台带来的是
          `project/a.md`，于是「会话删了 team 那一整棵」成立——批量删除的保险
          （`BULK_DELETE_*`）挡得住大半棵，挡不住一小棵；
        - 就算没被拦下，磁盘上会一直留着同名不同前缀的两份，其中 `team/` 那一份
          再也回不到平台。

        搬法是「先搬树、再改基线」：`team/` 下每个文件挪到 `project/` 的同名文件
        ——**目标已经存在就不覆盖**（那一定是平台刚铺下来的、更新的那一版），搬完
        把空掉的 `team/` 删掉；最后把基线键里的 `team/` 前缀改成 `project/`。基线
        必须一起改：它是三方合并里「上次铺下去的是什么」那一方，前缀不改就还是对
        不上号。

        幂等：搬完 `team/` 就不在了，下一次进来什么都不做。会话机上要是还跑着改名
        前的旧归档，它认的仍是 `team`，平台的 `project` 那一份照常铺下去、残留的
        `team/` 由 `BULK_DELETE_*` 兜着，下一轮起这段代码把它收尾。
        """
        legacy = root / LEGACY_SCOPE_DIR
        if legacy.is_dir():
            current = root / PROJECT_PREFIX
            for entry in sorted(legacy.iterdir()):
                if not entry.is_file():
                    continue
                target = current / entry.name
                if target.exists():
                    # 平台这一轮已经铺了更新的这一版，旧副本没有用了。
                    with contextlib.suppress(OSError):
                        entry.unlink()
                    continue
                try:
                    current.mkdir(parents=True, exist_ok=True)
                    os.replace(entry, target)
                except OSError:
                    continue
            with contextlib.suppress(OSError):
                legacy.rmdir()
        baseline = _recall_baseline(root)
        if not any(path.startswith(f"{LEGACY_SCOPE_DIR}/") for path in baseline):
            return
        moved = {
            (
                f"{PROJECT_PREFIX}/{path[len(LEGACY_SCOPE_DIR) + 1 :]}"
                if path.startswith(f"{LEGACY_SCOPE_DIR}/")
                else path
            ): fingerprint
            for path, fingerprint in baseline.items()
        }
        _write_memory(root, MEMORY_BASELINE, json.dumps(moved))

    def read_memory(self, managed: set[str]) -> tuple[dict[str, str], set[str]]:
        """受管作用域里现在有哪些文件、正文各是什么，和被写空了的那几条。

        一条记忆（索引以外）被写成空内容就是删掉了它：agent 手里能碰到这棵树的
        只有 Read / Write / Edit，shell 在另一台机器上，`rm` 碰不到这里。所以空
        的那一条不算「在」，对账把它当成会话删了（照样过批量删除那道闸）。索引
        写空是另一回事：那是一份空索引，照常收。

        只读一层、只收 `.md`、名字还得过 `check_scoped_path`：这棵树的形状是定死
        的（一个作用域一层，一条记忆一个文件），子目录里冒出来的东西、随手写下的
        临时文件都不是这条规矩的一部分，给它们一个身份等于替 agent 认了一条它没
        写过的记忆。
        """
        root = memory_root()
        found: dict[str, str] = {}
        emptied: set[str] = set()
        for prefix in sorted(managed):
            try:
                entries = sorted((root / prefix).iterdir())
            except OSError:
                continue
            for entry in entries:
                path = f"{prefix}/{entry.name}"
                try:
                    check_scoped_path(path)
                    if not entry.is_file():
                        continue
                    content = entry.read_text(encoding="utf-8")
                except (OSError, ValueError, UnicodeDecodeError):
                    continue
                if not content.strip() and entry.name != INDEX_NAME:
                    emptied.add(path)
                else:
                    found[path] = content
        return found, emptied

    def sync_memory(self, params: dict) -> dict:
        """对一次账：平台这一份铺下来，会话改过的带回去。

        一条记忆的两种改法都在这里收口：平台写的（别的会话、界面、dream）由
        `scopes` 进来，会话写的由磁盘进来，谁赢看 `tree.sync_tree` 那一条规矩。

        基线从**记忆树自己的根**上读、也写回那里（`MEMORY_BASELINE`）：它和这棵树
        同生同死，所以「树没了」不会被读成「会话删光了这棵树」。
        """
        scopes = memory_scopes(params)
        root = memory_root()
        # 改名那一轮：先把会话机上的 `team/` 搬成 `project/`，基线一起改，再对账。
        self._migrate_legacy_scope(root)
        baseline = _recall_baseline(root)
        managed = prefixes_of(scopes, baseline)
        disk, emptied = self.read_memory(managed)
        outcome = sync_tree(scopes=scopes, disk=disk, baseline=baseline)
        if outcome.held:
            # 拦下来的是「这一次没照做」：平台上一条都没少，会话里那几个文件下一轮
            # 会被重新铺回去。说出来，因为下一次对账看到的还是同一棵树——同一条会
            # 再响一次，而那正是「这件事一直没过去」。这个进程里没有 logger（它是
            # 会话机上那个只有标准库的归档），说进 stderr 就是 runner.log。
            print(
                f"{ended(self.launch)}: memory sync held {len(outcome.held)} "
                f"deletions (over {BULK_DELETE_MIN} and above "
                f"{BULK_DELETE_RATIO:.0%} of their scope): " + ", ".join(outcome.held),
                file=sys.stderr,
                flush=True,
            )
        # 没收的那几版先留到旁边：新建的那一条马上会被下面的清理删掉。
        for path in outcome.rejected:
            _write_memory(root, rejected_path(path), disk[path])
        for path, content in outcome.files.items():
            _write_memory(root, path, content)
        # 两方都没有、只有 baseline 里还有的那一条（平台删了，会话也没写回去），
        # 到这里才从磁盘上消失：先算完再删，删的不是「还没看过的东西」。
        # 写空了的那一条也在这里清掉，下一轮的树里就没有一个空壳；批量删除被拦下
        # 时它在 `outcome.files` 里，上面已经把平台那一版写回去了。
        for path in set(disk) | set(baseline) | emptied:
            if path in outcome.files:
                continue
            with contextlib.suppress(OSError):
                (root / path).unlink(missing_ok=True)
        self._keep_refused(root, outcome.refused)
        _write_memory(root, MEMORY_BASELINE, json.dumps(outcome.baseline))
        # `held` 跟着回去：会话这一侧的兜底挡下的那些删除，平台那一侧看不见
        # （`files` 里它们已经被放回去了）。整理那一轮要知道这件事——「这次删得
        # 太多」是它必须说出来的一句话，而不是只在会话机的 stderr 里响一次。
        return {
            "files": outcome.files,
            "refused": outcome.refused,
            "rejected": outcome.rejected,
            "held": list(outcome.held),
        }

    @staticmethod
    def _keep_refused(root: Path, refused: dict[str, str]) -> None:
        """被平台盖回去的那几版，就地留一份旁路文件（`<名字>.conflict.md`）。

        房间里那句话只说得出「有改动被盖回来了」，而那句话要落到 agent 手里它才能
        重读再写——它读到的是什么，取决于它还能不能看到自己刚写的那一版。留在同一
        个目录里，它下一步就是 Read 那个文件。删除（`REMOVED`，空串）不留：没有正
        文可以留，那句话本身已经把「你删的那条被平台留下了」说完。

        这一类文件**不是记忆**：名字不是 kebab-case，所以 `read_memory` 不收它、
        回写时也带不回数据库；索引里当然也不会有它——索引是 agent 写的，平台只
        认 `.md` 里那些合法名字。
        """
        for path, content in refused.items():
            if not content:
                continue
            # `project/a.md` → `project/a.conflict.md`：和 `memory_conflict_notice` 说给
            # agent 的那条路径一模一样，它照着那句话就能 Read 到。
            _write_memory(root, f"{path[:-3]}.conflict.md", content)

    async def _expire(self) -> None:
        while True:
            self.journal.expire(
                (datetime.now(UTC) - timedelta(seconds=RETENTION_S)).isoformat()
            )
            await asyncio.sleep(RETENTION_EVERY_S)

    def busy(self) -> bool:
        return bool(
            self.working or self.tasks or self.sent or self.controls or self.commands
        )

    async def yield_foreground(self) -> None:
        """Ctrl+B, as the build takes it on stdin: every foreground Bash and
        subagent returns to the model at once and goes on as a background task.
        """
        if self.working:
            await self.control({"subtype": "background_tasks"})

    async def _yield_again(self) -> None:
        # A control the session did not answer changes nothing it was doing.
        with contextlib.suppress(Exception):
            await self.yield_foreground()

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
        if how in ("send", "steer"):
            if self.session_id is None or work is None:
                raise ValueError("An input needs its native session and work identity")
            # Commit the association before stdin: a failed drain can still have
            # written the input, and an echo may arrive after runner replacement.
            self.journal.remember(
                f"receipt:{identifier}",
                json.dumps(
                    {"session_id": self.session_id, "work_id": work, "how": how}
                ),
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
        owes_reply: bool = False,
    ) -> dict:
        """A user message, or words said to a session that is working.

        Both are the same line on stdin: the build takes a message written
        mid-turn at the next tool boundary. They differ only in whose turn a
        message opens when the session happens to be idle by the time it reads
        it — ``steering`` says it was addressed to a turn already running.
        """
        how = "steer" if steering else "send"

        async def submit() -> dict:
            if not steering and not self.working:
                await self._catch_up()
            await self._put(identifier, work_id, text, images or [], how)
            return {"input_id": identifier}

        return await self.accept(
            identifier,
            {"text": text, "images": images or [], "work_id": work_id, "how": how},
            submit,
            owes_reply=owes_reply,
        )

    async def _catch_up(self) -> None:
        """A new turn starts on the project's skills as they are on the machine.

        Plain Claude Code watches its skill directories and picks up a skill
        added, edited or removed while it runs. A room's session reads the
        project's through a view of the executor that no watcher sees change,
        so before a turn the runner has the executor client synchronize the
        session's context (`client.py catch-up`, which relinks what changed),
        and when anything changed the session reloads its skills. Run from
        here and not through the session's own context service: that one
        listens inside the session's namespace, whose `/tmp` is its own. A
        failure costs the turn nothing: it starts on what the session has.
        """
        if not self.execution:
            return
        try:
            helper = json.loads(Path(self.execution).read_text())["helper"]
            process = await asyncio.create_subprocess_exec(
                *helper,
                "catch-up",
                self.execution,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(
                    process.communicate(), CATCH_UP_TIMEOUT_S
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                raise
            if process.returncode:
                raise RuntimeError(err.decode(errors="replace")[-600:])
            if json.loads(out or b"{}").get("changed"):
                await self.command("/reload-skills")
        except Exception as error:  # noqa: BLE001 — the turn goes on regardless
            print(
                f"project context not synchronized before the turn: {error!r}",
                file=sys.stderr,
                flush=True,
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
        if method == "events":
            after = int(params.get("after", 0))
            news = await self.news_for(after, params)
            return {"events": self.records(after), **news}
        if method in ("send", "steer"):
            return await self.send(
                params["input_id"],
                params["text"],
                images=params.get("images"),
                work_id=params.get("work_id"),
                steering=method == "steer",
                owes_reply=bool(params.get("owes_reply")),
            )
        if method == "interrupt":
            self.interrupting = self.working
            answer = await self.control({"subtype": "interrupt"})
            return {"interrupted": answer.get("subtype") == "success"}
        if method == "control":
            return await self.control(params["request"])
        if method == "command":
            return await self.command(params["text"])
        if method == "memory":
            # 不是 accept 那种一次性输入：对账是幂等的（同样的三方合出同样的结
            # 果），重来一次不会多出一条记忆，所以不需要按 id 去重。
            return self.sync_memory(params)
        if method == "reply_check":
            # The session's Stop hook (`client.py reply`), as the turn is about
            # to end. A message the build has not read yet opens a turn of its
            # own, and is that turn's to answer.
            if self.interrupting or (self.owed is not None and self.owed in self.sent):
                return {}
            reason = self.insist()
            return {"reason": reason} if reason else {}
        if method == "ping":
            return {
                "pid": os.getpid(),
                "input_protocol": INPUT_PROTOCOL,
                "session_id": self.session_id,
                "working": self.working,
                "work_id": self.work if self.working else None,
                "tasks": dict(self.tasks),
                "alive": self.alive(),
                "capabilities": list(self.capabilities),
            }
        raise ValueError(f"Unknown Claude Code session operation: {method}")
