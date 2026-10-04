"""pi 这一侧的会话进程：中心机上起的就是它，它再把 pi 起起来。

Started on the session host by the launch (`host.py`) and left running, as
Codex's runner is: it does one thing and then blocks — start the runner, and
stay alive until pi exits or it is told to stop. Everything a backend asks of it
arrives on the runner's socket, which outlives any particular backend.
"""

import argparse
import asyncio
import json
import os
import signal
from pathlib import Path

from app.domain.agent.harness.driven.runner import IDLE_EXIT_S, SessionStart
from app.domain.agent.harness.pi.runner import Runner


async def serve(state: Path, config: dict, *, binary: str, cwd: str) -> None:
    # How long an idle session is kept is the launch's to say: a person's 芝士
    # lets go sooner than a room (`personal.py`).
    runner = Runner(state, idle_exit_s=float(config.get("idle_exit_s", IDLE_EXIT_S)))
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    waits: list[asyncio.Task] = []
    try:
        await runner.start(
            SessionStart(**config["opening"]),
            binary=binary,
            cwd=cwd,
            env=dict(os.environ),
            args=config["args"],
            target=config["execution_target"],
            skills=config.get("skills"),
            extension=config.get("extension"),
            notice=config.get("notice", ""),
            tools=config.get("tools"),
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
    parser.add_argument("--config", type=Path, required=True)
    # Paths come on argv rather than in the config: that file carries the
    # room's system prompt, and a shell that had to interpolate paths into it
    # would have to re-read the prompt on its way past.
    parser.add_argument("--binary", required=True)
    parser.add_argument("--cwd", required=True)
    args = parser.parse_args()
    asyncio.run(
        serve(
            args.state,
            json.loads(args.config.read_text()),
            binary=args.binary,
            cwd=args.cwd,
        )
    )


if __name__ == "__main__":
    main()
