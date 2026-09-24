"""The screen's program for a Claude Code session: start the runner, then wait.

The launcher prepares the session home, the platform CLI, the remote-execution
helpers and the command that starts Claude Code, and then runs this. It starts
the runner and stays alive until Claude Code exits or the screen that supervises
it is taken down; everything a backend asks arrives on the runner's socket.

What to run arrives in the environment rather than on argv. The command is the
launcher's own shell string (the executor client's ``bootstrap`` in front of the
pinned binary and its flags), and the environment is the one channel into a
launch a remote machine builds for itself.
"""

import argparse
import asyncio
import os
import signal
from pathlib import Path

from app.domain.agent.harness.claude_code.runner import Runner, executor_command

COMMAND = "CHEESE_CLAUDE_COMMAND"


async def serve(state: Path) -> None:
    env = dict(os.environ)
    command = env.pop(COMMAND)
    runner = Runner(state, executor=executor_command(Path(env["HOME"])))
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        loop.add_signal_handler(sig, stopped.set)
    waits: list[asyncio.Task] = []
    try:
        await runner.start(
            command=command,
            env=env,
            resume=env.get("CHEESE_RESUME_SESSION") or None,
            agent_handle=env.get("CHEESE_AUTHOR") or None,
        )
        assert runner.process is not None
        process = asyncio.create_task(runner.process.wait())
        stop = asyncio.create_task(stopped.wait())
        waits = [process, stop]
        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in waits:
            task.cancel()
        try:
            await runner.close()
        finally:
            await asyncio.gather(*waits, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.state))


if __name__ == "__main__":
    main()
