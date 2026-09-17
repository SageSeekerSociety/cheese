"""Own a pi process on the machine, and outlive whoever is reading from it.

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
import contextlib
import fcntl
import hashlib
import json
import os
import signal
import sys
import uuid
from pathlib import Path

from app.domain.agent.harness import Opening
from app.domain.agent.harness.pi import catalog
from app.domain.agent.harness.pi.journal import Journal
from app.domain.agent.harness.pi.rpc import LINE_LIMIT, Connection

# A live event that can only mean an entry was written. Anything else is
# progress within a message, and the entry for it does not exist yet.
SETTLES = frozenset({"message_end", "turn_end", "agent_end", "agent_settled"})


def socket_path(state: Path) -> str:
    """Where the connector will look, given this state directory.

    The name is not ours to choose: ``cli/internal/host/executor.go`` derives it
    from the state directory the backend recorded and relays one JSON line each
    way. Matching it is what lets ``hub.call_executor`` reach this runner with no
    connector change at all — the relay was never about the executor, only about
    a socket named this way.
    """
    digest = hashlib.sha256(str(state.resolve()).encode()).hexdigest()[:24]
    return f"/tmp/cheese-execution-{os.getuid()}-{digest}.sock"


class Runner:
    def __init__(self, state: Path):
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state = state
        self.journal = Journal(state / "entries.sqlite")
        self.process: asyncio.subprocess.Process | None = None
        self.client: Connection | None = None
        self.listener: asyncio.Task | None = None
        self.server: asyncio.Server | None = None
        self.inputs: dict[str, asyncio.Task] = {}
        self.refreshing = asyncio.Lock()
        self.doorbell = asyncio.Event()
        self.refresher: asyncio.Task | None = None
        self.working = False
        self.errors = None
        self.lock = None

    # --- reading -------------------------------------------------------------

    async def observe(self, event: dict) -> None:
        """Ring the doorbell; never answer it here.

        This runs inside the read loop, and fetching entries is a request only
        that same loop can answer — doing it here is a process waiting on
        itself. So the reader only records that something happened.
        """
        kind = event.get("type")
        if kind == "agent_start":
            self.working = True
        elif kind == "agent_settled":
            self.working = False
        if kind in SETTLES:
            self.doorbell.set()

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
            owner = json.loads(self.journal.recall("owner") or "{}")
            while True:
                since = self.journal.recall("received")
                # A fresh session has no cursor, and pi REFUSES a null one
                # ("Entry not found: null") rather than reading it as "from the
                # start" — so the first pull asks without the field at all.
                fields = {"since": since} if since is not None else {}
                page = (await self.client.request("get_entries", **fields))["entries"]
                if not page:
                    return
                self.journal.import_entries(
                    [{**entry, "cheese": owner} for entry in page] if owner else page
                )
                if len(page) < 256:
                    return

    # --- the platform extension ----------------------------------------------

    def write_extension(self, files: dict[str, str], notice: str = "") -> Path:
        """Put the extension and its tool catalog on disk; answer with the entry.

        The catalog is built HERE, from the CLI installed on this machine,
        because that is the copy the calls will run against. A list shipped
        from the backend would be a claim about a file the backend cannot see,
        and the first thing to go wrong would be a tool the agent can name and
        the machine cannot run.

        A machine with no CLI still gets the extension: it has four more
        reasons to exist than the platform tools, and a room where the CLI
        failed to install is one where saying so beats loading nothing.
        """
        home = self.state / "extension"
        home.mkdir(parents=True, exist_ok=True)
        for name, content in sorted(files.items()):
            (home / name).write_text(content, encoding="utf-8")
        cli = catalog.cli_path()
        try:
            tools = catalog.tools(cli) if cli is not None else []
            reason = "" if cli is not None else f"no {catalog.CLI} on PATH"
        except Exception as error:  # noqa: BLE001 — a room still opens without them
            tools, reason = [], f"{type(error).__name__}: {error}"
        (home / "platform.json").write_text(
            json.dumps(
                {
                    "socket": socket_path(self.state),
                    "state": str(self.state),
                    # A backgrounded command has to survive this session, so
                    # what starts it is a script and an interpreter, not a
                    # thread. Both named here because the runner is the side
                    # that knows: it was started by that interpreter and it
                    # just wrote that script.
                    "python": sys.executable,
                    "background": str(home / "background.py"),
                    "jobs": str(self.state / "bg"),
                    "tools": tools,
                    "unavailable": reason,
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

    async def run_cli(self, tool: str, arguments: dict, cwd: str | None) -> dict:
        """One platform tool call, as the CLI would have been typed.

        argparse is the authority twice over: it says what the arguments mean,
        and ``catalog.argv`` re-parses what it built, so a call that could not
        have been typed fails here rather than reaching the CLI as a malformed
        command line.
        """
        source = catalog.cli_path()
        if source is None:
            raise RuntimeError(f"{catalog.CLI} is not installed on this machine")
        process = await asyncio.create_subprocess_exec(
            str(source),
            *catalog.argv(source, tool, arguments),
            cwd=cwd or None,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=LINE_LIMIT,
        )
        out, err = await process.communicate()
        return {
            "status": process.returncode,
            "stdout": out.decode("utf-8", "replace"),
            "stderr": err.decode("utf-8", "replace"),
        }

    # --- lifecycle -----------------------------------------------------------

    async def start(
        self,
        opening: Opening,
        *,
        binary: str,
        cwd: str,
        env: dict[str, str],
        args: list[str],
        skills: dict[str, str] | None = None,
        extension: dict[str, str] | None = None,
        notice: str = "",
    ) -> str:
        self.lock = (self.state / "runner.lock").open("a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        saved = self.journal.recall("session_id")
        if saved is not None and opening.resume_token not in (None, saved):
            raise ValueError("A session directory cannot resume a different session")
        session_id = saved or opening.resume_token or str(uuid.uuid4())
        self.journal.remember("session_id", session_id)
        self.journal.remember(
            "owner",
            json.dumps({"harness": "pi", "agent_handle": opening.agent_handle}),
        )
        # Only the lock owner may remove a socket a crashed runner left behind.
        Path(socket_path(self.state)).unlink(missing_ok=True)
        # The room's system prompt reaches pi as a FILE it is pointed at, never
        # as argv: it is assembled per room and runs to multiple KB, and argv is
        # both size-capped and readable by anyone who can list processes.
        prompt = self.state / "system-prompt.md"
        prompt.write_text(opening.system_prompt, encoding="utf-8")
        appended = (
            ["--append-system-prompt", str(prompt)] if opening.system_prompt else []
        )
        # pi starts with `--no-skills` because it would otherwise read whatever
        # the machine's owner keeps in ~/.agents and cwd. Explicit `--skill`
        # paths are additive even then, so the platform's own skills are written
        # here and named — for the same reason the system prompt is: they are
        # assembled by the platform, and the machine has no copy to point at.
        for relative, content in sorted((skills or {}).items()):
            skill = self.state / relative
            skill.parent.mkdir(parents=True, exist_ok=True)
            skill.write_text(content, encoding="utf-8")
            appended += ["--skill", str(skill.parent)]
        if extension is not None:
            home = self.write_extension(extension, notice)
            appended += ["--extension", str(home / "index.ts")]
            # Named rather than derived: an extension that had to work out
            # where it was written would be guessing at a path the runner
            # already knows, and the first thing a wrong guess costs is every
            # platform tool in the room.
            env = {**env, "CHEESE_PI_EXTENSION": str(home)}
        self.errors = (self.state / "pi.log").open("ab")
        self.process = await asyncio.create_subprocess_exec(
            binary,
            "--mode",
            "rpc",
            "--session-id",
            session_id,
            "--session-dir",
            str(self.state / "sessions"),
            *appended,
            *args,
            cwd=cwd,
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
        self.refresher = asyncio.create_task(self._answer_the_door())
        # Whatever the session already holds is ours to land before the first
        # input: resuming a session means its history is already there.
        await self.refresh()
        self.server = await asyncio.start_unix_server(
            self.handle, path=socket_path(self.state), limit=LINE_LIMIT
        )
        os.chmod(socket_path(self.state), 0o600)
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
    ) -> dict:
        """Put one input in, at most once, however many times we are asked.

        The identifier is the platform's, and it is what makes a reconnection
        safe: a backend that never saw our answer resends the same id and gets
        the same outcome rather than a second turn.
        """
        payload = json.dumps(
            {
                "text": text,
                "images": images or [],
                "work_id": work_id,
                "steer": steering,
            },
            sort_keys=True,
        )
        previous = self.journal.input(identifier)
        if previous is not None:
            if previous[0] != payload:
                raise ValueError("An input ID cannot be reused for different text")
            if previous[1] == "accepted":
                return previous[2] or {}
            if previous[1] == "failed":
                raise RuntimeError((previous[2] or {})["error"])
            if identifier not in self.inputs:
                raise RuntimeError(
                    "Previous input outcome is unresolved; it was not resubmitted"
                )
        else:
            self.journal.begin_input(identifier, payload)
            task = asyncio.create_task(
                self._submit(identifier, text, steering, images, work_id)
            )
            self.inputs[identifier] = task

            def finished(task):
                self.inputs.pop(identifier, None)
                if not task.cancelled():
                    task.exception()  # Failure is retained in the input journal.

            task.add_done_callback(finished)
        return await asyncio.shield(self.inputs[identifier])

    async def _submit(
        self,
        identifier: str,
        text: str,
        steering: bool,
        images: list[dict] | None,
        work_id: str | None,
    ) -> dict:
        assert self.client is not None
        try:
            if work_id is not None and not steering:
                # Persist attribution before the call: entries can appear
                # before the command's own acknowledgement comes back.
                owner = json.loads(self.journal.recall("owner") or "{}")
                self.journal.remember(
                    "owner", json.dumps({**owner, "work_id": work_id})
                )
            fields: dict = {"message": text}
            if images:
                fields["images"] = images
            if steering:
                await self.client.request("steer", **fields)
            else:
                await self.client.request("prompt", **fields)
            result = {"input_id": identifier}
            self.journal.finish_input(identifier, "accepted", result)
            return result
        except Exception as error:
            self.journal.finish_input(identifier, "failed", {"error": str(error)})
            raise

    # --- the socket ----------------------------------------------------------

    async def dispatch(self, method: str, params: dict) -> dict:
        if method == "entries":
            await self.refresh()
            since = params.get("since")
            after = self.journal.sequence_of(since) if since else 0
            return {"entries": [row["entry"] for row in self.journal.read(after)]}
        if method == "send":
            return await self.send(
                params["input_id"],
                params["text"],
                images=params.get("images"),
                work_id=params.get("work_id"),
            )
        if method == "steer":
            return await self.send(
                params["input_id"],
                params["text"],
                steering=True,
                images=params.get("images"),
                work_id=params.get("work_id"),
            )
        if method == "cli":
            return await self.run_cli(
                params["tool"], params.get("arguments") or {}, params.get("cwd")
            )
        if method == "abort":
            if self.client is None:
                return {"aborted": False}
            await self.client.request("abort")
            self.working = False
            return {"aborted": True}
        if method == "ping":
            owner = json.loads(self.journal.recall("owner") or "{}")
            return {
                "pid": os.getpid(),
                "session_id": self.journal.recall("session_id"),
                "working": self.working,
                "work_id": owner.get("work_id"),
                "alive": self.process is not None and self.process.returncode is None,
            }
        raise ValueError(f"Unknown pi session operation: {method}")

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request = json.loads(await reader.readline())
            response = {
                "result": await self.dispatch(
                    request["method"], request.get("params", {})
                )
            }
        except Exception as error:
            response = {"error": str(error)}
        try:
            writer.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")
            await writer.drain()
        except (ConnectionError, BrokenPipeError):
            # A disconnected backend does not cancel the accepted input.
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, BrokenPipeError):
                await writer.wait_closed()

    def end_background_jobs(self) -> None:
        """Take down what the room started, now that the room is going.

        A backgrounded command is deliberately not killed at the end of a turn —
        that is the whole point of it. But it is not the machine's to keep
        either: this runner IS the screen's program, so when it goes the room
        is being torn down, and a dev server nobody can reach any more would
        hold its port until somebody found it by hand.

        What is signalled is the COMMAND's process group, not the supervisor's.
        The two are different sessions — that separation is what lets a job
        outlive pi — so a signal aimed at the supervisor would leave the command
        running with nothing left holding its name. Signalled this way the
        supervisor sees its child go, drains what it printed on the way out and
        records the exit, which is also what a reader needs afterwards.
        """
        jobs = self.state / "bg"
        if not jobs.is_dir():
            return
        for job in jobs.iterdir():
            if (job / "exit").exists():
                continue
            try:
                meta = json.loads((job / "meta.json").read_text())
                os.killpg(os.getpgid(meta["child"]), signal.SIGTERM)
            except (OSError, ValueError, KeyError):
                # Already gone, never written, or ours no longer to signal.
                continue

    async def close(self) -> None:
        self.end_background_jobs()
        if self.refresher is not None:
            self.refresher.cancel()
            await asyncio.gather(self.refresher, return_exceptions=True)
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
        try:
            if self.listener is not None:
                with contextlib.suppress(Exception):
                    await self.listener
        finally:
            await asyncio.gather(*self.inputs.values(), return_exceptions=True)
            if self.errors is not None:
                self.errors.close()
            self.journal.close()
            if self.server is not None:
                Path(socket_path(self.state)).unlink(missing_ok=True)
            if self.lock is not None:
                self.lock.close()
