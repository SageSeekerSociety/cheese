"""Own one headless Claude Code on the session machine, and outlive its readers.

Claude Code runs as ``claude -p`` with stream-json on both pipes, and this
process holds the pipes for the whole life of the session: a user message, a
message said mid-work and a control are all a line written to its stdin, and
everything it reports is a line read from its stdout. Closing stdin is the
ending, so it is closed exactly once, on purpose — when the session has sat
idle (``driven.runner``), or when the screen that supervises this runner goes
away. Claude Code reads that close as the session's last input and exits with
the status the session would have had, and what it still had running is stopped
for it (``release``) — from 2.1.292 it waits for its background tasks before it
leaves, which a released session must not do.

What the session does is reported in the lifecycle protocol
(``driven/lifecycle.py``), because only this side sees it happen: an input
written to stdin, its echo coming back (``--replay-user-messages``) as the one
proof the build read it, a ``command_lifecycle`` that settles one unread, the
session starting work (the echo of an input, or a record nobody asked for when a
background task woke it) and going idle at ``result`` with how that work ended,
and the process exiting. The stuck-work clocks run here too, on what the session
writes as it writes it.

Every line read is also recorded in the journal under a stable sequence, stamped
with the room's work it belongs to and, on a record that brings a tool's result
back, with what that call was — except the deltas of the block being written,
which are only shown while it is written (``stream``). The journal is what the
room shows and the platform archives; nothing is decided from it.

One thing Claude Code does not put on stdout is read from disk into the same
journal: the transcripts of agents a subagent or a workflow starts, which only
their own ``subagents/**/agent-<id>.jsonl`` file holds.

Standard library only: this module travels in the runner archive.
"""

import asyncio
import contextlib
import json
import os
import shutil
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
from app.domain.agent.harness.driven import lifecycle, runner

if sys.platform == "win32":
    # Shipped at the archive's root (`driven/bundle.py`).
    import portable
from app.domain.memory.files import (
    INDEX_NAME,
    MEMORY_ROOT,
    check_scoped_path,
    conflict_path,
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
#: How long a launch must keep running before a greeting takes it as started
#: (``Runner.starting``). A failing bootstrap or a missing program ends well
#: inside it; a session that comes up costs a greeting no more than this.
SETTLE_S = 0.5
#: How long a released session gets to end on its own before what it left
#: running is stopped for it (``Runner.release``). A session with nothing
#: running ends at once and never waits this out.
RELEASE_GRACE_S = 5.0
CONTROL_TIMEOUT_S = 30.0
COMMAND_TIMEOUT_S = 120.0
#: How long the runner waits for a compaction it asked the build for to report
#: its `result`. Summarising a full window takes minutes; this is the outer
#: bound on believing it will come at all, not an expected duration.
COMPACT_TIMEOUT_S = 1800.0
# How long a new turn waits to see the project's context as it is now before it
# starts on what the session already has.
CATCH_UP_TIMEOUT_S = 30.0
TAIL_POLL_S = 0.5
# How long the runner keeps what it recorded, and how often it looks.
RETENTION_S = 7 * 24 * 3600
RETENTION_EVERY_S = 3600
# How many works' endings the ping carries (``Runner.endings``): enough that a
# drain polling every few seconds sees the work it waits for even when the
# runner opened and ended a few more of its own in between.
ENDED_KEPT = 16
# A tool result read from a transcript file is not capped the way stdout is.
# The room reads the tail of a failure and a subagent's conclusion; neither is
# worth more than stdout would have carried.
FINISHED = frozenset({"completed", "failed", "killed", "stopped"})
# What the build says of an input it will never echo now (``command_lifecycle``):
# each is the one terminal state that input gets in this session.
SETTLED = frozenset({"completed", "cancelled", "discarded", "refused"})
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
#: The launch whose agent process has not had its ending reported yet: set when
#: a process starts, cleared once its ``exited`` is in the outbox. Still set
#: when a runner starts, it names a process that died with its runner.
LIVE_LAUNCH = "live_launch"
#: The epoch that process was registered under (``register``); empty until it
#: is. Its ending, reported for it by the next runner, carries this epoch.
LIVE_EPOCH = "live_epoch"
#: The platform inputs written to the agent and not read yet, in the order they
#: were written: what is dropped when the process goes.
UNREAD_INPUTS = "unread_inputs"
#: How far the platform has taken the journal (``read``'s ``records_after``).
RECORDS_TAKEN = "records_taken"
# Where the session's own helpers reach this runner. The same name as
# `executor_transport.SESSION_SOCKET`, which reads it: that file ships to the
# session host on its own and cannot import this one.
SESSION_SOCKET = "CHEESE_SESSION_SOCKET"


def ended(launch: str) -> str:
    return f"cheese-runner {launch} ended"


#: How a launch's record of its ending says Claude Code never came up: what the
#: backend reads to tell a session that did not start from one that ended.
NEVER_STARTED = "before it started"


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


#: Where a runner on Windows names the agent it started: there a process's
#: environment cannot be read, so `sessions_on` has nothing to find it by.
AGENT_FILE = "agent.json"


def remember_agent(state: Path, pid: int) -> None:
    """Name the agent this runner started, for the next runner here (Windows)."""
    assert sys.platform == "win32"
    started = portable.created(pid)
    (state / AGENT_FILE).write_text(json.dumps({"pid": pid, "started": started}))


def end_left_agent(state: Path) -> None:
    """End the agent an earlier runner here started and left running: a runner
    on Windows that dies does not take its children with it (Windows)."""
    assert sys.platform == "win32"
    try:
        left = json.loads((state / AGENT_FILE).read_text())
    except (OSError, ValueError):
        return
    pid, started = left["pid"], left.get("started")
    pids = portable.tree([(pid, started)])
    if portable.created(pid) != started:
        # The pid names a later process now; only what the agent left counts.
        pids = [p for p in pids if p != pid]
    portable.terminate(pids)


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


def started_by(session: int) -> list[int]:
    """What a session started and could still be running: its descendants.

    A session's work is under it — the shell it started a command in, what that
    shell runs, and the helpers it started for itself — so its descendants are
    what has to be stopped for it to be let go of work it would otherwise wait
    out (``Runner.release``). Its own process is not in the list: it is the one
    being ended, and it should be the one to exit.
    """
    if sys.platform == "win32":
        return [
            pid
            for pid in portable.tree([(session, portable.created(session))])
            if pid != session
        ]
    children: dict[int, list[int]] = {}
    for pid, parent in _processes():
        children.setdefault(parent, []).append(pid)
    found: list[int] = []
    queue = [session]
    while queue:
        for child in children.get(queue.pop(), []):
            found.append(child)
            queue.append(child)
    return found


def _processes() -> list[tuple[int, int]]:
    """Every process on this machine as (pid, parent), from /proc where there is
    one and from ``ps`` where there is not."""
    proc = Path("/proc")
    if proc.is_dir():
        pairs = []
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                # Past the name, which may itself hold a ')'.
                _, parent = (entry / "stat").read_bytes().split(b")")[-1].split()[:2]
            except (OSError, ValueError):
                continue
            pairs.append((int(entry.name), int(parent)))
        return pairs
    try:
        listing = subprocess.run(
            ["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    pairs = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0].isdigit() and fields[1].isdigit():
            pairs.append((int(fields[0]), int(fields[1])))
    return pairs


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
#: 会话刚刚删掉的，一次对账就把 team 和发言人的 private 整棵删光——删除没有历史
#: 可以恢复。放在这里，家一没这张表跟着没，下一次对账就是一次全新的铺。
MEMORY_BASELINE = ".baseline.json"

#: 索引的底稿（路径 → 上一次对账后那一份索引的正文），和基线放在一起、同生同死。
#: 基线只有指纹，而索引两边都动过时要按行合并（`tree.merge_lines`），合并要的是
#: 上次那一版的正文。只存索引：别的记忆两边都动过时是平台赢，用不着底稿。
MEMORY_BASES = ".bases.json"


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


def _recall_baseline(root: Path, name: str = MEMORY_BASELINE) -> dict[str, str]:
    """上一次对账留下的那张表（就在记忆树根上）：指纹表（`MEMORY_BASELINE`），
    或者索引底稿（`MEMORY_BASES`）。

    读不出来当没写过——一次对账从头铺一遍，比拿着一张读不懂的表去判「谁改过」安
    全。读不到也正是「这棵树是新的」：家被重建过，于是每一条都按平台的版本铺。底
    稿读不到，索引这一次就照普通文件对账（平台赢）。
    """
    try:
        baseline = json.loads((root / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(baseline, dict):
        return {}
    return {
        str(path): str(value)
        for path, value in baseline.items()
        if isinstance(path, str) and isinstance(value, str)
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


def file_entry(entry: dict) -> dict | None:
    """Keep native messages, including complete subagent tool results and images."""
    if entry.get("type") not in ("user", "assistant"):
        return None
    return {
        key: entry[key]
        for key in ("type", "uuid", "timestamp", "agentId", "isSidechain", "message")
        if key in entry
    }


#: How often the runner looks at its stuck-work clocks (``lifecycle.Watchdog``).
WATCH_S = 5.0
#: How long a runner whose agent process ended keeps answering, so the
#: platform can take the events that say so. What it has not taken by then is
#: still in the journal, and the next runner on this state delivers it.
LINGER_S = 30.0
#: How many finished background tasks the snapshot keeps listing.
TASKS_KEPT = 50


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def usage_of(record: dict) -> dict | None:
    """What a ``result`` reports it used: every token it sent, cached or not,
    and what the build prices it at. The model is the one that did most of it.
    """
    usage = record.get("usage")
    if not isinstance(usage, dict):
        return None
    by_model = record.get("modelUsage")
    model = ""
    if isinstance(by_model, dict) and by_model:
        model = max(
            by_model,
            key=lambda name: (by_model[name] or {}).get("outputTokens") or 0,
        )
    cost = record.get("total_cost_usd")
    return {
        "model": model,
        "input_tokens": _count(usage.get("input_tokens"))
        + _count(usage.get("cache_creation_input_tokens"))
        + _count(usage.get("cache_read_input_tokens")),
        "output_tokens": _count(usage.get("output_tokens")),
        "cost_usd": float(cost) if isinstance(cost, int | float) else 0.0,
    }


def task_state(record: dict, current: dict) -> dict:
    """A background task as it stands after one of the build's ``task_*``
    records: its fields as the build last said them, ``patch`` applied."""
    update = {
        name: value
        for name, value in record.items()
        if name not in ("cheese", "uuid", "session_id", "type", "patch")
        and value is not None
    }
    update.update(record.get("patch") or {})
    subtype = record.get("subtype")
    if subtype == "task_started" and "status" not in update:
        update["status"] = "running"
    if subtype == "task_notification":
        update["status"] = record.get("status") or "completed"
    return {**current, **update}


class Runner(runner.Runner[Journal]):
    def __init__(
        self,
        state: Path,
        *,
        idle_exit_s: float = runner.IDLE_EXIT_S,
        launch: str = "",
    ):
        super().__init__(state, Journal, "records.sqlite", idle_exit_s=idle_exit_s)
        # Names this process in what it reports of its own ending; a runner
        # started by hand has none from the platform, and still needs one.
        self.launch = launch or uuid.uuid4().hex
        self.write_lock = asyncio.Lock()
        self.controls: dict[str, asyncio.Future] = {}
        self.commands: dict[str, asyncio.Future] = {}
        # Inputs written and not yet echoed: uuid → (kind, work id).
        self.sent: dict[str, tuple[str, str | None]] = {}
        self.working = False
        self.work: str | None = None
        self.unsolicited = False
        self.last_work: str | None = None
        # The run this session is in, or was last in: the (epoch, seq) of the
        # ``running`` status event that began it, the identity the platform
        # applies that event under. A platform tool call carries it
        # (``dispatch`` ``run``), so a report says which run it was made in.
        self.run: tuple[int, int] | None = None
        # How the last few works ended: each one's work id, typed failure
        # (None when it ended cleanly) and the platform's inputs it read,
        # oldest first. Kept for the ping, which a release that cannot read
        # this protocol drains this runner by (``lifecycle``).
        self.endings: list[dict] = []
        self.interrupting = False
        # What the session reports, as ordered events (``lifecycle``).
        self.outbox = lifecycle.Outbox(self.journal.connection)
        self.watchdog = lifecycle.Watchdog()
        # What looks at those clocks, while the session works.
        self.watcher: asyncio.TimerHandle | None = None
        # The clock that ended the work under way, once one has.
        self.stuck: str | None = None
        # The platform's inputs read since the session last went idle: what a
        # failure of that work names.
        self.run_inputs: list[str] = []
        # The session asked to compact its own context (`dispatch`'s `compact`)
        # and the turn it asked in has not ended yet: `/compact` is written
        # when it has.
        self.compact_when_idle = False
        # What the build said of the work under way that its ``result`` does
        # not: the API's refusal, and the account's rate-limit window.
        self.api_error = ""
        self.rate_limit: dict | None = None
        # The session as it stands (``state``): its readiness and its tasks.
        self.ready: dict = {}
        self.task_states: dict[str, dict] = {}
        # Harness tasks still running, by id → task type.
        self.tasks: dict[str, str] = {}
        # What each tool call in flight was, by its id, for the records that
        # bring its result back (``_stamp_calls``).
        self.calls: dict[str, dict] = {}
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
        # Claude Code itself wrote a record: what failed after that was the
        # build, not the executor client's bootstrap in front of it.
        self.ran = False
        self.settling: asyncio.Future | None = None
        self.exit_reported = False
        self.launched_at: float | None = None
        # The index of the content block being shown (``stream``).
        self.streaming: int | None = None
        # How long each API call waited for its first token, by the message id
        # it answered with, until the record that call's blocks land in takes
        # it (``stream`` / ``observe``).
        self.waits: dict[str, int] = {}
        # The last frame read off the child's stdout, for the readout of what
        # a session is doing (`heartbeat`). None until a frame, and the work is
        # the one in flight when THAT frame arrived — never `last_work`, so a
        # finished run cannot be reported as a live one.
        self.last_frame_kind: str | None = None
        #: Wall clock and monotonic clock of that frame. The monotonic one is
        #: this machine's own; the age it yields is advanced by the backend on
        #: its own clock after receipt.
        self.last_frame_at: float | None = None
        self.last_frame_monotonic: float | None = None
        self.last_frame_work: str | None = None

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
        if sys.platform == "win32":
            end_left_agent(self.state)
        else:
            end(sessions_on(self.config_dir, agent_handle))
        self._report_left_exit()
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
            # asked for; still here after Claude Code's own failing exit, it
            # names a transcript this build cannot continue, and the next start
            # begins afresh instead of dying on it again (`close`).
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
        self.endings = json.loads(self.journal.recall("endings") or "[]")
        self.errors = (self.state / "claude.log").open("ab")
        # Earlier starts appended here too; this start's words begin here.
        self.errors_from = self.errors.tell()
        self.launched_at = time.monotonic()
        # Until this process reports its own ending, the next runner here
        # reports it for it (`_report_left_exit`).
        self.journal.remember(LIVE_LAUNCH, self.launch)
        self.journal.remember(LIVE_EPOCH, str(self.outbox.epoch or ""))
        self.process = await asyncio.create_subprocess_exec(
            # CreateProcess looks in System32 before PATH; Git Bash's sh is on PATH.
            shutil.which("sh") or "sh" if sys.platform == "win32" else "sh",
            "-c",
            f"exec {command} {flag}",
            # The session's own helpers reach this runner here, to change the
            # session while it runs (`executor_transport.register_project_hooks`).
            env=self.agent_env({**env, SESSION_SOCKET: runner.socket_path(self.state)}),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self.errors,
            limit=LINE_LIMIT,
            # A session of its own, so ending it ends what it started; Windows
            # has none and ends the tree instead (`end_left_agent`, procscreen).
            start_new_session=sys.platform != "win32",
        )
        if sys.platform == "win32":
            remember_agent(self.state, self.process.pid)
        self.listener = asyncio.create_task(self._read())
        self.helpers = [
            asyncio.create_task(self._tail()),
            asyncio.create_task(self._expire()),
        ]
        await self.listen(LINE_LIMIT)
        return self.session_id

    def starting(self) -> bool:
        """Still inside ``SETTLE_S`` of the launch, and not yet answered.

        The launch command is a shell string (the executor client's bootstrap
        in front of Claude Code), so a process is running the moment it is
        spawned, whether or not what it runs will come up: a missing program
        or a refused work lease ends it a moment later. A greeting answered in
        that moment would take the session as started and lose the reason it
        ended. Until the launch has outlived that moment, or Claude Code has
        answered, the greeting is told to keep asking.
        """
        return (
            self.alive()
            and not self.proven
            and self.launched_at is not None
            and time.monotonic() - self.launched_at < SETTLE_S
        )

    async def stopped(self) -> None:
        with contextlib.suppress(Exception):
            await super().stopped()

    async def close(self) -> None:
        if self.watcher is not None:
            self.watcher.cancel()
        for helper in self.helpers:
            helper.cancel()
        await asyncio.gather(*self.helpers, return_exceptions=True)
        if self.process is not None and self.process.returncode is None:
            # Ended here, as the screen that supervises this runner goes away:
            # its ending is reported before the journal closes.
            self.process.terminate()
            await self.process.wait()
        await self._ending()
        await super().close()

    async def linger(self, within: float = LINGER_S) -> None:
        """Keep answering after the agent process ended, until the platform
        has taken every event, or ``within`` runs out. A runner the platform
        never registered has nobody to wait for: what it reported stays in its
        journal for the next runner here."""
        if not self.outbox.epoch:
            return
        deadline = time.monotonic() + within
        while not self.outbox.empty() and time.monotonic() < deadline:
            news = self.news
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    news.wait(), max(0.0, min(1.0, deadline - time.monotonic()))
                )

    async def _announce_exit(self, process: asyncio.subprocess.Process) -> None:
        # The backend reads why it never came up as soon as it hears the
        # process is gone (`Driver.last_words`), so that is written first.
        await process.wait()
        await asyncio.shield(self._ending())
        self.announce()

    def _ending(self) -> asyncio.Future:
        """Settle what the process's ending says, once, whether its exit or a
        close comes first."""
        if self.settling is None:
            self.settling = asyncio.ensure_future(self._settle_ending())
        return self.settling

    async def _settle_ending(self) -> None:
        # Claude Code that exited by itself, failing, before it ever answered
        # was handed a transcript it cannot continue. An exit we asked for, a
        # clean one, or the bootstrap failing before Claude Code ran (its lease,
        # its machine) says nothing about the transcript.
        process = self.process
        failed = (
            process is not None
            and process.returncode not in (None, 0)
            and not self.proven
        )
        # Whatever the process still wrote is read before its ending is told.
        await self.stopped()
        said = ""
        with contextlib.suppress(Exception):
            resuming = self.journal.recall("resuming")
            if failed and resuming and self.ran:
                self.journal.remember("resume_failed", resuming)
            self.journal.remember("resuming", "")
        if failed and process is not None:
            said = self._last_words(process.returncode)
        with contextlib.suppress(Exception):
            self._report_exit(process.returncode if process else None, said)

    def _report_exit(self, status: int | None, said: str) -> None:
        """The agent process is gone: what it had not read it never will, the
        work it was in the middle of failed, and the process exited."""
        if self.exit_reported:
            return
        self.exit_reported = True
        self._drop_unread("exited")
        if self.working:
            self._finish(
                failure={
                    "kind": lifecycle.EXITED_MIDWAY,
                    "status": status,
                    "said": said,
                },
                text="",
                usage=None,
            )
        self._emit(lifecycle.EXITED, launch=self.launch, exit_code=status, said=said)
        self.journal.remember(LIVE_LAUNCH, "")
        self.journal.remember(LIVE_EPOCH, "")
        self.announce()

    def _report_left_exit(self) -> None:
        """The runner before this one on this state ended without reporting
        its process's ending (it was killed with it): report it for it, so
        what that process had not read is settled and its exit is known."""
        left = self.journal.recall(LIVE_LAUNCH)
        if not left:
            return
        # Under the epoch that process was registered under: these are about
        # it, and the registration this runner will get is not its.
        epoch = int(self.journal.recall(LIVE_EPOCH) or lifecycle.NEVER_REGISTERED)
        self._drop_unread("exited", epoch=epoch)
        self._emit(lifecycle.EXITED, launch=left, exit_code=None, said="", epoch=epoch)
        self.journal.remember(LIVE_LAUNCH, "")
        self.journal.remember(LIVE_EPOCH, "")

    def _last_words(self, status: int | None) -> str:
        """Say why Claude Code never came up, where the backend will look.

        Its socket goes with this runner, so a backend still waiting for the
        session can only read the runner's own log (``channel._ended``). The reason
        is in Claude Code's stderr — the executor client's ``bootstrap`` runs in
        front of the binary and fails there, before any model is asked — and
        without this the room was told only that the socket was missing.
        """
        said = ""
        with contextlib.suppress(OSError):
            with (self.state / "claude.log").open("rb") as log:
                end = log.seek(0, os.SEEK_END)
                log.seek(max(self.errors_from, end - LAST_WORDS))
                said = log.read().decode("utf-8", "replace").strip()
        record = f"Claude Code exited with status {status} {NEVER_STARTED}" + (
            f":\n{said}" if said else ", and said nothing"
        )
        print(f"{ended(self.launch)}: {record}", file=sys.stderr, flush=True)
        return record

    # --- what the session reports --------------------------------------------

    def _emit(self, kind: str, *, epoch: int | None = None, **body) -> int:
        """Report one fact, after every record journaled so far; ``epoch``
        for a fact about an earlier process (``Outbox.emit``). Returns its
        seq."""
        return self.outbox.emit(kind, records=self.journal.last(), epoch=epoch, **body)

    def _unread(self) -> list[str]:
        return json.loads(self.journal.recall(UNREAD_INPUTS) or "[]")

    def _set_unread(self, inputs: list[str]) -> None:
        self.journal.remember(UNREAD_INPUTS, json.dumps(inputs))

    def _taken(self, identifier: str) -> bool:
        """Whether ``identifier`` was a platform input still unread; it is not
        one any more."""
        self.watchdog.taken(identifier)
        unread = self._unread()
        if identifier not in unread:
            return False
        self._set_unread([value for value in unread if value != identifier])
        return True

    def _drop(self, identifier: str, reason: str, *, epoch: int | None = None) -> None:
        """Report that the agent will never read ``identifier``, under the
        session and work it was written for."""
        written = json.loads(self.journal.recall(f"receipt:{identifier}") or "{}")
        self._emit(
            lifecycle.INPUT,
            epoch=epoch,
            input_id=identifier,
            status=lifecycle.DROPPED,
            session_id=written.get("session_id") or self.session_id,
            work_id=written.get("work_id"),
            reason=reason,
        )

    def _drop_unread(self, reason: str, *, epoch: int | None = None) -> None:
        for identifier in self._unread():
            self.watchdog.taken(identifier)
            self._drop(identifier, reason, epoch=epoch)
        self._set_unread([])

    def standing(self) -> dict:
        """The session as it stands now: its worker status, what it came up
        with, and its background tasks — every running one and the last
        ``TASKS_KEPT`` finished."""
        running = {
            key: task
            for key, task in self.task_states.items()
            if task.get("status") == "running"
        }
        finished = [key for key, task in self.task_states.items() if key not in running]
        return {
            "worker_status": lifecycle.RUNNING if self.working else lifecycle.IDLE,
            "session_id": self.session_id,
            "ready": self.ready,
            "tasks": {
                **running,
                **{key: self.task_states[key] for key in finished[-TASKS_KEPT:]},
            },
            "heartbeat": self.heartbeat(),
        }

    def has_news(self, after: int) -> bool:
        return super().has_news(after) or not self.outbox.empty()

    def _watching(self) -> None:
        """Have the clocks looked at every ``WATCH_S`` while the session
        works, when one is on: a timer, not a task, so a session that is not
        working holds nothing open for them."""
        if self.working and self.watchdog.armed() and self.watcher is None:
            self.watcher = asyncio.get_running_loop().call_later(WATCH_S, self._look)

    def _look(self) -> None:
        """End work one of the stuck-work clocks says is stuck: the agent is
        interrupted, and the ``status`` its ending writes names the clock."""
        self.watcher = None
        if not self.working:
            return
        fired = None if self.stuck else self.watchdog.fired(time.monotonic())
        if fired is not None:
            self.stuck = fired
            print(
                f"work {self.work} stuck ({fired}); interrupting it",
                file=sys.stderr,
                flush=True,
            )
            self.helpers.append(asyncio.ensure_future(self._interrupt_stuck()))
        self._watching()

    async def _interrupt_stuck(self) -> None:
        # A control the session did not answer changes nothing it was doing;
        # the clock looks again, and the work ends when the session does.
        with contextlib.suppress(Exception):
            await self.control({"subtype": "interrupt"})

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
            self.ran = True
            self._note_frame(record)
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
                # left unanswered would hold the work for ever, so it is refused.
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

    def _note_frame(self, record: dict) -> None:
        """Remember the last frame read, before any branch decides its fate.

        Every legal record counts, including the ones that are never journaled
        (`keep_alive`, `thinking_tokens`, streamed deltas). The work is taken
        from the frame's own moment — a frame read while nothing is in flight
        says "no work", so it cannot later be attributed to a work that
        started afterwards.
        """
        subtype = record.get("subtype")
        kind = record.get("type")
        self.last_frame_kind = (
            str(subtype if kind == "system" and subtype else kind)
            if (subtype or kind)
            else None
        )
        self.last_frame_at = time.time()
        self.last_frame_monotonic = time.monotonic()
        self.last_frame_work = self.work if self.working else None

    def heartbeat(self) -> dict:
        """The last frame read, and how long ago, for the readout of what a
        session is doing. Says nothing about whether the runner is reachable:
        that is every answer itself.

        `work_id` is the work that frame belonged to, and only while that work
        is still the one in flight.
        """
        recorded = self.last_frame_work
        work = recorded if self.working and recorded == self.work else None
        age = (
            None
            if self.last_frame_monotonic is None
            else round(time.monotonic() - self.last_frame_monotonic, 2)
        )
        return {
            "session_id": self.session_id,
            "work_id": work,
            "last_frame_kind": self.last_frame_kind,
            "last_frame_age_s": age,
        }

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
        if kind == "message_start":
            # How long this call waited before its first token. The build says
            # it here and nowhere else, and a streamed event is never
            # journaled, so the number waits for the record this call's blocks
            # land as (``observe``): that record is where a wait belongs, one
            # per call, with what the wait was for.
            wait = record.get("ttft_ms")
            identifier = (event.get("message") or {}).get("id")
            if isinstance(wait, int) and not isinstance(wait, bool) and identifier:
                self.waits[str(identifier)] = wait
            return
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

    def command_ran(self, call: str) -> int | None:
        """How long the command behind one Bash call ran, as the executor
        timed it and the shell prefix left it here (``shell_prefix``): the
        part of the call's wall clock the command itself spent."""
        if not self.execution:
            return None
        path = Path(self.execution).parent / "command-timings" / f"{call}.json"
        try:
            ran = json.loads(path.read_text()).get("ran_ms")
        except (OSError, ValueError):
            return None
        return ran if isinstance(ran, int) and not isinstance(ran, bool) else None

    def _watch_marks(self, message: dict) -> dict:
        """What one transcript-shaped record says to the stuck-work clocks,
        and the calls whose results it brings back (``cheese.calls``)."""
        now = time.monotonic()
        content = (message.get("message") or {}).get("content")
        blocks = content if isinstance(content, list) else []
        returned: dict = {}
        if message.get("type") == "assistant" and not message.get("error"):
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and str(block.get("text") or "").strip():
                    self.watchdog.output(now)
                elif block.get("type") == "tool_use":
                    call = str(block.get("id"))
                    given = block.get("input")
                    arguments = given if isinstance(given, dict) else {}
                    self.calls[call] = {
                        "name": str(block.get("name") or ""),
                        "description": str(arguments.get("description") or ""),
                    }
                    self.watchdog.tool_started(call, now)
        elif message.get("type") == "user" and not message.get("isReplay"):
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    call = str(block.get("tool_use_id"))
                    self.watchdog.tool_returned(call, now)
                    if call in self.calls:
                        returned[call] = self.calls.pop(call)
        return returned

    def observe(self, record: dict, *, from_file: bool = False) -> None:
        """Report what one record changes, stamp it with the work it belongs
        to, and journal it."""
        kind = record.get("type")
        command = (
            self._sent_input(str(record.get("command_uuid")))
            if kind == "command_lifecycle"
            else None
        )
        main = not from_file and record.get("parent_tool_use_id") is None
        if kind == "system" and record.get("subtype") == "init":
            self.proven = True
            self.ready = {
                key: record.get(key)
                for key in ("model", "permissionMode", "tools", "mcp_servers")
            }
        if kind == "command_lifecycle" and record.get("state") == "started":
            # The build takes an input: the one moment that says which input a
            # piece of work is for. A message said mid-work is taken at a tool
            # boundary inside the work it was said to; one the session reads
            # only after that work ended starts work of its own.
            if not self.working:
                self._open(command)
        elif kind == "command_lifecycle" and record.get("state") in SETTLED:
            # An input is on its way in until its echo, but work aborted
            # before its prompt was checked never echoes it: it will never be
            # read, and the platform may offer it again (``input_holds``).
            identifier = str(record.get("command_uuid"))
            self.sent.pop(identifier, None)
            if self._taken(identifier):
                self._drop(identifier, str(record.get("state")))
            if not self.working and self.owed == identifier:
                self.reply_settled()
        elif main and kind == "user" and record.get("isReplay"):
            identifier = str(record.get("uuid"))
            sent = self._sent_input(identifier)
            self.sent.pop(identifier, None)
            if not self.working:
                self._open(sent)
            if self._taken(identifier):
                self.run_inputs.append(identifier)
                self._emit(
                    lifecycle.INPUT,
                    input_id=identifier,
                    status=lifecycle.READ,
                    session_id=self.session_id,
                    work_id=sent[1] if sent else None,
                    execution_work_id=self.work,
                )
        elif main and not self.working and kind in ("assistant", "user"):
            # Nothing of ours started this. A background task finished and the
            # notification woke the session.
            self._open(None)
        if kind == "rate_limit_event" and isinstance(
            info := record.get("rate_limit_info"), dict
        ):
            # Kept for the work's end: a window that refused the account says
            # when it resets, and that decides what the room is told.
            self.rate_limit = info
        message = record.get("entry") if kind == "cheese_file" else record
        stamp: dict = {}
        if isinstance(message, dict):
            if main and message.get("type") == "assistant":
                # This call's wait, on the first block it landed as: the same
                # message id is written once per finished block, and the wait
                # belongs to the call, not to each of them.
                answer = (message.get("message") or {}).get("id")
                wait = self.waits.pop(str(answer), None) if answer else None
                if wait is not None:
                    stamp["first_token_ms"] = wait
                if message.get("error"):
                    # The API refused the work; the result that follows says
                    # only that it failed, and this is the one record that says
                    # how.
                    self.api_error = str(message["error"])
                for block in (message.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        self.main_calls.add(str(block.get("id")))
            if returned := self._watch_marks(message):
                for call, told in returned.items():
                    # What the command itself took, where the shell prefix on
                    # this host left it: the rest of the call's wall clock is
                    # what the platform spent carrying it to the executor.
                    ran = self.command_ran(call)
                    if ran is not None:
                        told["ran_ms"] = ran
                stamp["calls"] = returned
        if kind == "system":
            self._track(record)
        work = self.work if self.working else self.last_work
        if kind == "command_lifecycle":
            if command and command[1] is not None:
                stamp["input_work_id"] = command[1]
            # Queueing or settling an input does not prove it executed in any
            # work. Only started names the execution it actually joined.
            if record.get("state") != "started" or (
                command is not None and command[0] == "internal"
            ):
                work = None
        owner = json.loads(self.journal.recall("owner") or "{}")
        owner.pop("work_id", None)
        owner.pop("unsolicited", None)
        if work is not None:
            owner["work_id"] = work
            if self.working and self.unsolicited and work == self.work:
                owner["unsolicited"] = True
        self.journal.append({**record, "cheese": {**owner, **stamp}})
        if main and kind in ("assistant", "result"):
            # The block being shown has landed, or the work ended without it.
            self.show([], None)
        if kind == "result":
            self.watchdog.progress(time.monotonic())
            waiting = {str(record.get("user_message_uuid"))} | {
                str(identifier) for identifier in record.get("user_message_uuids") or []
            }
            for identifier in waiting:
                future = self.commands.pop(identifier, None)
                if future is not None and not future.done():
                    future.set_result(record)
            self._finish(
                failure=self._failure(record),
                text="" if record.get("is_error") else str(record.get("result") or ""),
                usage=usage_of(record),
            )

    def _failure(self, record: dict) -> dict | None:
        """How the work a ``result`` ends failed, typed, or None for a clean
        ending. The kind and its evidence only: what to make of it — whether
        to try again, when, at whose cost — is the platform's.

        Failure is ``is_error`` and only that: an API error arrives with
        subtype "success" and terminal_reason "api_error".
        """
        if self.stuck is not None:
            return {"kind": lifecycle.STUCK, "stuck": self.stuck}
        if self.interrupting:
            return {"kind": lifecycle.INTERRUPTED}
        if not record.get("is_error"):
            return None
        status = record.get("api_error_status")
        status = status if isinstance(status, int) else None
        told = [str(error) for error in record.get("errors") or [] if str(error)]
        reset = (self.rate_limit or {}).get("resetsAt")
        return {
            "kind": lifecycle.API_ERROR
            if self.api_error or status is not None
            else lifecycle.ERROR,
            "status": status,
            "code": self.api_error
            or str(record.get("terminal_reason") or record.get("subtype") or ""),
            "errors": told,
            # A run that fails before it asks the model anything (a
            # conversation it cannot resume) carries no result, only the
            # build's own words for it.
            "detail": str(record.get("result") or "").strip() or "\n".join(told),
            "reset_at": reset
            if (self.rate_limit or {}).get("status") == "rejected"
            and isinstance(reset, int | float)
            else None,
        }

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
        """The session starts working, on the input that woke it (None: no
        input of ours)."""
        how, work = sent or ("", None)
        if how == "send":
            self.working, self.work, self.unsolicited = True, work, False
        elif how == "internal":
            # A platform command (`/reload-plugins`): no room's work.
            self.working, self.work, self.unsolicited = True, None, False
        else:
            # The session's own work: a background task woke it, a notice the
            # runner wrote did, or a message it read only after the work it was
            # said to had ended.
            self.working, self.work, self.unsolicited = True, str(uuid.uuid4()), True
        self.run_inputs = []
        self.watchdog.started(time.monotonic())
        self._watching()
        seq = self._emit(
            lifecycle.STATUS,
            worker_status=lifecycle.RUNNING,
            session_id=self.session_id,
            work_id=self.work,
            unsolicited=self.unsolicited,
        )
        # Written before any registration, the event takes the first epoch
        # this runner is registered under (``Outbox.register``); so does this.
        self.run = (self.outbox.epoch, seq)

    def _finish(self, *, failure: dict | None, text: str, usage: dict | None) -> None:
        """The session went idle: report how the work ended, then forget it."""
        if self.work is not None:
            ending = {
                "work": self.work,
                "failure": failure,
                "inputs": list(self.run_inputs),
            }
            self.endings = [*self.endings, ending][-ENDED_KEPT:]
            self.journal.remember("endings", json.dumps(self.endings))
        self._emit(
            lifecycle.STATUS,
            worker_status=lifecycle.IDLE,
            session_id=self.session_id,
            work_id=self.work,
            unsolicited=self.unsolicited,
            result={
                "text": text,
                "usage": usage,
                "failure": failure,
                "inputs": list(self.run_inputs),
                "rate_limit": self.rate_limit,
            },
        )
        self._end()

    def _end(self) -> None:
        if self.work is not None:
            self.last_work = self.work
            self.journal.remember("last_work", self.work)
        self.working, self.work, self.unsolicited = False, None, False
        self.interrupting = False
        self.stuck = None
        self.api_error = ""
        self.rate_limit = None
        self.run_inputs = []
        self.watchdog.stopped()
        # A person's message the build has not taken yet starts the next
        # work, and it is that work which owes the answer.
        if self.owed not in self.sent:
            self.reply_settled()
        if self.compact_when_idle and not self.closing:
            self.compact_when_idle = False
            self.helpers.append(asyncio.ensure_future(self._compact_now()))

    def _track(self, record: dict) -> None:
        """What is running, and which agents only their own file reports on."""
        task = str(record.get("task_id") or "")
        subtype = record.get("subtype")
        if task and str(subtype).startswith("task_"):
            self.task_states[task] = task_state(
                record, self.task_states.get(task, {"task_id": task})
            )
            self.announce()
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
        baseline = _recall_baseline(root)
        managed = prefixes_of(scopes, baseline)
        disk, emptied = self.read_memory(managed)
        outcome = sync_tree(
            scopes=scopes,
            disk=disk,
            baseline=baseline,
            bases=_recall_baseline(root, MEMORY_BASES),
        )
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
        _write_memory(root, MEMORY_BASES, json.dumps(outcome.bases))
        _write_memory(root, MEMORY_BASELINE, json.dumps(outcome.baseline))
        # `held` 跟着回去：会话这一侧的兜底挡下的那些删除，平台那一侧看不见
        # （`files` 里它们已经被放回去了）。整理那一轮要知道这件事——「这次删得
        # 太多」是它必须说出来的一句话，而不是只在会话机的 stderr 里响一次。
        return {
            "files": outcome.files,
            "refused": outcome.refused,
            "rejected": outcome.rejected,
            "unindexed": list(outcome.unindexed),
            "held": list(outcome.held),
        }

    @staticmethod
    def _keep_refused(root: Path, refused: dict[str, str]) -> None:
        """被平台盖回去的那几版，就地留一份旁路文件（`<名字>.conflict.md`）。

        房间里那句话只说得出「有改动被盖回来了」，而那句话要落到 agent 手里它才能
        重读再写——它读到的是什么，取决于它还能不能看到自己刚写的那一版。留在同一
        个目录里，它下一步就是 Read 那个文件。删除（`REMOVED`，空串）不留：没有正
        文可以留，那句通知自己说了「你那一版是删掉它，没有副本」。

        这一类文件**不是记忆**：名字不是 kebab-case，所以 `read_memory` 不收它、
        回写时也带不回数据库；索引里当然也不会有它——索引是 agent 写的，平台只
        认 `.md` 里那些合法名字。
        """
        for path, content in refused.items():
            if not content:
                continue
            # 名字取自 `files.conflict_path`：`memory_conflict_notice` 叫
            # agent 去读的那份副本，就是这里落下的这一份。
            _write_memory(root, conflict_path(path), content)

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
        """Let the session go the way it is meant to: close its stdin, and stop
        what it left running.

        Closing stdin is the ending. Nothing running turns out of it at once;
        a session still working on something of its own — which is what the
        build waits out before it goes, from 2.1.292 on — is let go by having
        that work stopped for it, and it then exits with the status it would
        have had. Only a session that will not go even then is ended on the
        spot, which the room reads as the ending it was already asking for.
        """
        assert self.process is not None and self.process.stdin is not None
        with contextlib.suppress(ConnectionError, BrokenPipeError):
            self.process.stdin.close()
            await self.process.stdin.wait_closed()
        if await self._gone(RELEASE_GRACE_S):
            return
        # Waiting out what is stopped is not done in the loop: reading the
        # session's stdout and answering a backend both go on meanwhile.
        await asyncio.to_thread(end, started_by(self.process.pid))
        if await self._gone(RELEASE_GRACE_S):
            return
        await asyncio.to_thread(end, [self.process.pid])
        await self._gone(RELEASE_GRACE_S)

    async def _gone(self, within: float) -> bool:
        """Whether the session process has exited, waiting up to ``within``."""
        assert self.process is not None
        try:
            await asyncio.wait_for(self.process.wait(), within)
        except TimeoutError:
            return False
        return True

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
            # Written is said before the write: its echo can be read before
            # the write returns, and a write that fails half-way may still
            # have put the input in. Unread it stays until the build says
            # otherwise — read, settled unread, or gone with the process.
            self._set_unread([*self._unread(), identifier])
            self.watchdog.written(identifier, time.monotonic())
            self._emit(
                lifecycle.INPUT,
                input_id=identifier,
                status=lifecycle.WRITTEN,
                session_id=self.session_id,
                work_id=work,
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
        except Exception as error:  # the turn goes on regardless
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

    def request_compact(self) -> dict:
        """The session asked to compact its own context: `/compact`, written
        once the turn it asked in has ended.

        Written then, and not mid-turn: a tool call in flight is answered at a
        tool boundary, and the build takes what comes in on stdin at those
        boundaries, so a slash command written from inside a tool call arrives
        as part of that turn — where the pinned build's contract says nothing
        about what a slash command does. A tool call is always inside a turn,
        so the request always defers.

        Once a turn is the whole rate limit: a request still waiting and one
        that has already run look the same to the agent that asked, and a
        second `/compact` would only summarise the first one's output.
        """
        if self.compact_when_idle:
            return {"already": True}
        self.compact_when_idle = True
        # A tool call is answered inside a turn, so `_end` is where this
        # normally goes. A session already idle has no turn coming.
        if not self.working and not self.closing:
            self.compact_when_idle = False
            self.helpers.append(asyncio.ensure_future(self._compact_now()))
        return {"requested": True, "already": False}

    async def _compact_now(self) -> None:
        """Write `/compact` the way `/reload-skills` is written, and wait for
        the command's `result` in the background.

        Not through `dispatch`: the tool call that asked has been answered
        already, and a compaction of a full window takes minutes to report.
        What the room shows meanwhile comes from the build's own
        `system/status` records (`events._compacting`)."""
        try:
            await self.command("/compact", COMPACT_TIMEOUT_S)
        except Exception as error:  # the session goes on without it
            print(f"compaction not asked: {error!r}", file=sys.stderr, flush=True)

    # --- the socket ----------------------------------------------------------

    async def dispatch(self, method: str, params: dict) -> dict:
        if method == "register":
            # The platform names this runner's epoch, and the clocks it ends
            # stuck work by (``lifecycle``).
            self.outbox.register(int(params["epoch"]))
            if self.run is not None and self.run[0] == 0:
                self.run = (self.outbox.epoch, self.run[1])
            if self.journal.recall(LIVE_LAUNCH) == self.launch:
                # The live process is this registration's from now on: its
                # ending, if the next runner reports it, carries this epoch.
                self.journal.remember(LIVE_EPOCH, str(self.outbox.epoch))
            clocks = params.get("watchdog") or {}
            self.watchdog.configure(
                no_progress_s=float(clocks.get("no_progress_s") or 0),
                unread_s=float(clocks.get("unread_s") or 0),
            )
            self._watching()
            self.announce()
            return {"worker_epoch": self.outbox.epoch, "state": self.standing()}
        if method == "ping":
            # The handshake every release reads, whichever protocol it speaks
            # (``lifecycle``): ``alive``, ``lifecycle``, ``launch``,
            # ``session_id``, ``working`` (the busy flag), ``work_id``,
            # ``worker_epoch`` and ``endings`` keep their names and meanings
            # (``endings``' entries their ``work``, ``failure`` and ``inputs``).
            # A later protocol
            # may add fields here, never drop or change these, so the release
            # after it can still tell whether this runner is at work, drain it
            # before replacing it, and know how the work it drained ended and
            # what it read.
            return {
                "pid": os.getpid(),
                "lifecycle": lifecycle.PROTOCOL,
                "launch": self.launch,
                "worker_epoch": self.outbox.epoch,
                "session_id": self.session_id,
                "working": self.working,
                "work_id": self.work if self.working else None,
                "endings": list(self.endings),
                "tasks": dict(self.tasks),
                "alive": self.alive(),
                "starting": self.starting(),
                "capabilities": list(self.capabilities),
            }
        if method == "apply_hooks":
            # The session's own helper (`executor_transport.register_project_
            # hooks`), not the platform: it holds no registration.
            return await self.control(
                {
                    "subtype": "apply_flag_settings",
                    "settings": {"hooks": params["hooks"]},
                }
            )
        if method == "reply_check":
            # The session's Stop hook (`client.py reply`), as the work is
            # about to end. A message the build has not read yet starts work
            # of its own, and is that work's to answer.
            if self.interrupting or (self.owed is not None and self.owed in self.sent):
                return {}
            reason = self.insist()
            return {"reason": reason} if reason else {}
        if method == "run":
            # The session's own helper making a platform tool call
            # (`executor_transport.PlatformHost`), asking which run the call
            # is made in. A call while the session is idle (a background
            # task's) is the last run's. Before any registration the run has
            # no identity the platform could have applied, so none is given.
            if self.run is None or not self.run[0]:
                return {}
            epoch, seq = self.run
            return {"run": f"{CLAUDE_CODE}/{epoch}/{seq}"}
        if method == "checkpoint_turn":
            # The session's Stop checkpoint (`client.py checkpoint`), asking
            # which turn it is about to sync. `work` while the turn is ending,
            # `last_work` once it has ended.
            work = self.work or self.last_work
            return {"work_id": work} if work else {}
        # Everything else is the platform's, and only the registration it
        # holds now may ask it.
        self.outbox.check(params.get("epoch"))
        if method == "read":
            return await self.read(params)
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
        if method == "compact":
            return self.request_compact()
        if method == "memory":
            # 不是 accept 那种一次性输入：对账是幂等的（同样的三方合出同样的结
            # 果），重来一次不会多出一条记忆，所以不需要按 id 去重。
            return self.sync_memory(params)
        raise ValueError(f"Unknown Claude Code session operation: {method}")

    async def read(self, params: dict) -> dict:
        """One read of the session: the events past what the platform
        acknowledged (``ack``), the journal past what it took
        (``records_after``; the last it took when it does not say), and the
        session as it stands. Asked to wait, it waits for news.

        The journal is the data plane: the session's records, kept for the
        room to show and the platform to archive. Nothing in it decides
        anything; the events do.
        """
        self.outbox.ack(int(params.get("ack") or 0))
        after = params.get("records_after")
        if after is None:
            after = int(self.journal.recall(RECORDS_TAKEN) or 0)
        else:
            after = int(after)
            self.journal.remember(RECORDS_TAKEN, str(after))
        news = await self.news_for(after, params)
        return {
            "worker_epoch": self.outbox.epoch,
            "events": self.outbox.pending(),
            "records": self.records(after),
            "state": self.standing(),
            **news,
        }
