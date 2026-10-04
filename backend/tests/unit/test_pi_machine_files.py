"""The files pi's tools take on the room's machine, through the runner.

Any file the machine's account can reach, as Claude Code's Read and Write
reach any; and on a machine whose executor predates these operations, a
plain answer that it has not been upgraded yet rather than a failure nobody
can read."""

import asyncio
import base64
from pathlib import Path

import pytest

from app.domain.agent.harness.driven.runner import SessionStart
from app.domain.agent.harness.pi.runner import Runner
from tests.support.room_machine import room_machine
from tests.unit.test_pi_runner import call, shim


def _with_runner(tmp_path: Path, work, *, before=None):
    async def session():
        runner = Runner(tmp_path / "state")
        with room_machine(tmp_path / "machine") as target:
            await runner.start(
                SessionStart("system prompt", None, agent_handle="teammate"),
                binary=shim(tmp_path),
                cwd=str(tmp_path),
                env={"PATH": "/usr/bin:/bin"},
                args=[],
                target=target,
            )
            try:
                if before is not None:
                    before(runner)
                return await work(runner)
            finally:
                await runner.close()

    return asyncio.run(session())


def test_a_file_outside_the_checkout_is_read_and_written(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "build.log").write_text("FAILED: 3 tests\n")

    async def work(runner):
        read = await call(
            runner.state,
            "files",
            {"operation": "read", "path": str(elsewhere / "build.log")},
        )
        await call(
            runner.state,
            "files",
            {
                "operation": "write",
                "path": str(elsewhere / "notes.md"),
                "data": base64.b64encode(b"noted\n").decode(),
            },
        )
        return base64.b64decode(read["data"])

    assert _with_runner(tmp_path, work) == b"FAILED: 3 tests\n"
    assert (elsewhere / "notes.md").read_text() == "noted\n"


def test_an_executor_not_yet_upgraded_says_so(tmp_path):
    def old_executor(runner):
        call_executor = runner.machine.client.call

        def answered(method, params=None, **options):
            answer = call_executor(method, params, **options)
            if method == "ping":
                answer = {
                    **answer,
                    "capabilities": [
                        c for c in answer["capabilities"] if c != "machine_files"
                    ],
                }
            return answer

        runner.machine.client.call = answered

    async def work(runner):
        with pytest.raises(RuntimeError) as refused:
            await call(runner.state, "files", {"operation": "read", "path": "a.md"})
        return str(refused.value)

    said = _with_runner(tmp_path, work, before=old_executor)
    assert "旧版本" in said and "升级" in said
