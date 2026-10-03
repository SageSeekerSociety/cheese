"""后台任务：命令在执行机上跑，runner 把它说的话抄到会话这一侧的文件里。

A job is one of the room machine's commands (`relay.py` holds its terminal), and
the extension's job tools only ever read files on the session host
(`platform.ts`). This runner is what connects the two: it starts the command,
reads what it prints from where the executor keeps it, and writes it down here,
one directory per job:

    meta.json   what was run, under which command, and which runner copies it
    output      everything the terminal has shown      (append-only)
    cursor      how far a reader has got               (the reader's)
    exit        status and when                        (written once, last)
    error       why the copying stopped                (only when that is so)

A runner that comes back — a new release, an idle session started again —
picks each unfinished job up where its output file ends: the executor's offsets
are the bytes already written here.

Standard library only: this runs in the runner archive on the session host.
"""

import asyncio
import contextlib
import json
import os
import shlex
import signal
import sys
import time
import uuid
from pathlib import Path

from app.domain.agent.harness.pi.machine import script_text

#: How long one read of a job waits on the machine for more output.
READ_WAIT_S = 10.0
#: How long a start waits for a command that fails at once to say so.
SETTLE_S = 0.7
#: How long a pump waits before asking again a machine that did not answer.
RETRY_S = 5.0


def _fifo(command_id: str) -> str:
    """Where a job's keyboard is on the machine: short by construction, since
    the same name is spelled into every write."""
    return f"${{TMPDIR:-/tmp}}/cheese-pi-{command_id}.in"


class Jobs:
    def __init__(self, machine, directory: Path):
        self.machine = machine
        self.directory = directory
        self.pumps: dict[str, asyncio.Task] = {}

    def _dir(self, job: str) -> Path:
        # Names come from us, never from the model, so a job id cannot address
        # a path (`platform.ts` refuses the same way).
        if not job or not all(c.isalnum() or c == "-" for c in job):
            raise ValueError(f"no such job: {job}")
        return self.directory / job

    def _meta(self, job: str) -> dict:
        return json.loads((self._dir(job) / "meta.json").read_text())

    @staticmethod
    def _finish(directory: Path, status: int, error: str = "") -> None:
        if error:
            (directory / "error").write_text(error, encoding="utf-8")
        partial = directory / f"exit.{os.getpid()}"
        partial.write_text(json.dumps({"status": status, "at": time.time()}))
        os.replace(partial, directory / "exit")

    async def start(self, command: str, label: str, cwd: str) -> dict:
        """Start `command` on the machine in its own terminal; its id."""
        command_id = "job-" + uuid.uuid4().hex[:16]
        job = f"job-{uuid.uuid4().hex[:8]}"
        directory = self._dir(job)
        directory.mkdir(parents=True)
        (directory / "output").write_bytes(b"")
        (directory / "meta.json").write_text(
            json.dumps(
                {
                    "command": command,
                    "label": label,
                    "cwd": cwd,
                    # Who copies it: a reader that finds this runner gone and
                    # no `exit` knows the job is not being followed.
                    "pid": os.getpid(),
                    "remote": command_id,
                    "started_at": time.time(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        try:
            await asyncio.to_thread(
                self.machine.start,
                command_id,
                "exec python3 - "
                + " ".join([_fifo(command_id), shlex.quote(cwd), shlex.quote(command)]),
                cwd=cwd,
                stdin=script_text("relay").encode(),
                # In the shell every harness runs the model's commands in, so
                # the job sees what the user's profile sets up.
                shell=True,
            )
        except BaseException as error:
            self._finish(directory, -1, f"{type(error).__name__}: {error}")
            raise
        self.pumps[job] = asyncio.create_task(self._pump(job, command_id, 0))
        await asyncio.sleep(SETTLE_S)
        return {"id": job}

    async def _pump(self, job: str, command_id: str, offset: int) -> None:
        directory = self._dir(job)
        while True:
            try:
                read = await asyncio.to_thread(
                    self.machine.read, command_id, offset, wait=READ_WAIT_S
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 — the machine may come back
                print(f"[cheese] job {job} unread: {error}", file=sys.stderr)
                await asyncio.sleep(RETRY_S)
                continue
            if read["data"]:
                with (directory / "output").open("ab") as output:
                    output.write(read["data"])
            offset = read["offset"]
            if "exit" in read:
                self._finish(directory, int(read["exit"]))
                return
            if read.get("lost"):
                self._finish(
                    directory, -1, "执行机上的这条命令丢了：执行服务在它结束前重启过"
                )
                return

    def running(self) -> int:
        """How many jobs this runner is still following."""
        return sum(not task.done() for task in self.pumps.values())

    def resume(self) -> None:
        """Pick up every job a previous runner left unfinished."""
        if not self.directory.is_dir():
            return
        for directory in sorted(self.directory.iterdir()):
            job = directory.name
            if (directory / "exit").exists() or job in self.pumps:
                continue
            try:
                command_id = self._meta(job)["remote"]
            except (OSError, ValueError, KeyError):
                continue
            meta = self._meta(job)
            meta["pid"] = os.getpid()
            (directory / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8"
            )
            output = directory / "output"
            offset = output.stat().st_size if output.exists() else 0
            self.pumps[job] = asyncio.create_task(self._pump(job, command_id, offset))

    async def write(self, job: str, text: str) -> None:
        """Type `text` into a running job's terminal."""
        directory = self._dir(job)
        if (directory / "exit").exists():
            status = json.loads((directory / "exit").read_text())["status"]
            # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
            raise RuntimeError(f"{job} 已结束，退出码 {status}")
        fifo = _fifo(self._meta(job)["remote"])
        code, said = await asyncio.to_thread(
            self.machine.run,
            f'[ -p "{fifo}" ] || {{ echo "{job} 已经不在终端上了"; exit 3; }}; '
            f'cat > "{fifo}"',
            stdin=text.encode(),
        )
        if code != 0:
            raise RuntimeError(said.decode("utf-8", "replace").strip())

    async def signal(self, job: str, number: int) -> None:
        await asyncio.to_thread(self.machine.signal, self._meta(job)["remote"], number)

    async def close(self, *, end: bool) -> None:
        """Stop copying; with `end`, stop the jobs themselves first.

        A job is deliberately not stopped when a turn ends — that is the whole
        point of it. But it is not the machine's to keep once the room's session
        is gone either: a dev server nobody can reach any more would hold its
        port until somebody found it by hand."""
        if end:
            for job in list(self.pumps):
                with contextlib.suppress(Exception):
                    await self.signal(job, signal.SIGTERM)
        for task in self.pumps.values():
            task.cancel()
        await asyncio.gather(*self.pumps.values(), return_exceptions=True)
        self.pumps.clear()
