"""The pi runner archive the session host runs."""

from pathlib import Path

from app.domain.agent.harness.driven import bundle

#: The platform's CLI (`catalog.py`): its tool table and its argparse tree, one
#: stdlib-only file that lives outside `app/`, shipped as it is rather than as a
#: second copy of either.
PLATFORM_TOOLS = Path(__file__).resolve().parents[5] / "sandbox" / "cheese"


def build() -> bytes:
    return bundle.build(
        "app.domain.agent.harness.pi.entry",
        (
            # The CLI's argparse tree, turned into tool schemas and back.
            "domain/agent/cli_worker.py",
            # `RemoteClient`: every file, command, hook and MCP call of the
            # session goes to the room's machine, or the platform, through it.
            "domain/agent/executor_transport.py",
            # The input marker the runner reads back for exact attribution
            # (FB-56) — stdlib-only, so the machine needs nothing else.
            "domain/agent/nonce.py",
            "domain/agent/harness/pi/rpc.py",
            "domain/agent/harness/pi/journal.py",
            "domain/agent/harness/pi/catalog.py",
            "domain/agent/harness/pi/hooks.py",
            "domain/agent/harness/pi/machine.py",
            "domain/agent/harness/pi/jobs.py",
            "domain/agent/harness/pi/mcp.py",
            # Run on the room's machine, read from the archive as source.
            "domain/agent/harness/pi/repository.py",
            "domain/agent/harness/pi/project_skills.py",
            "domain/agent/harness/pi/relay.py",
            "domain/agent/harness/pi/subagents.py",
            "domain/agent/harness/pi/runner.py",
            "domain/agent/harness/pi/entry.py",
            "domain/agent/harness/pi/host.py",
        ),
        extra={
            "app/domain/agent/harness/pi/cheese.py": PLATFORM_TOOLS.read_text(),
        },
    )
