"""Standalone session owner; the connector supervises this process by its socket."""

import argparse
import asyncio
import json
import os
import signal
from pathlib import Path

from app.domain.agent.harness.codex.runner import Runner
from app.domain.agent.harness.codex.tools import RemoteTools
from app.domain.agent.harness.driven.runner import SessionStart, reply_owed_path


async def serve(state: Path, config: dict) -> None:
    tools = RemoteTools(
        config["execution_target"],
        mirror=state / "project-skills",
        reply_file=reply_owed_path(state),
        shipped=state / "platform-skills",
    )
    # What a session in this project gets besides the repository's skills,
    # as the Claude Code and pi launches carry them (`session_skill_files`).
    await asyncio.to_thread(tools.ship_skills, config.get("skills") or {})
    runner = Runner(state, tools, skills=tools)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    waits = []
    try:
        schemas = await tools.discover()
        tools.main_thread = await runner.start(
            SessionStart(**config["opening"]),
            binary=config["binary"],
            cwd=config["cwd"],
            env=dict(os.environ),
            tools=schemas,
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
    args = parser.parse_args()
    asyncio.run(serve(args.state, json.loads(args.config.read_text())))


if __name__ == "__main__":
    main()
