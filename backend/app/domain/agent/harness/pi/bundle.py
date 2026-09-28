"""The pi runner archive the session machine runs."""

from app.domain.agent.harness.driven import bundle


def build() -> bytes:
    return bundle.build(
        "app.domain.agent.harness.pi.entry",
        (
            # The platform CLI's own argparse tree, whole: it is one
            # standard-library-only file, and a second copy of the mapping between
            # a command and a tool schema is the thing worth carrying it to avoid.
            "domain/agent/cli_worker.py",
            # `RemoteClient`, which carries a remote MCP server's calls to the
            # platform the way Codex's tools do (`mcp.py`).
            "domain/agent/executor_transport.py",
            "domain/agent/harness/pi/rpc.py",
            "domain/agent/harness/pi/journal.py",
            "domain/agent/harness/pi/catalog.py",
            "domain/agent/harness/pi/hooks.py",
            "domain/agent/harness/pi/mcp.py",
            "domain/agent/harness/pi/runner.py",
            "domain/agent/harness/pi/entry.py",
        ),
    )
