"""The Codex runner archive sent to the session host."""

from pathlib import Path

from app.domain.agent.harness.driven import bundle

#: The platform's tool table (`tools.platform_tools`): one stdlib-only file that
#: lives outside `app/`, shipped as it is rather than as a second copy of its
#: schemas.
PLATFORM_TOOLS = Path(__file__).resolve().parents[5] / "sandbox" / "cheese"


def build() -> bytes:
    return bundle.build(
        "app.domain.agent.harness.codex.entry",
        (
            "domain/agent/executor_transport.py",
            "domain/agent/harness/codex/app_server.py",
            "domain/agent/harness/codex/session.py",
            "domain/agent/harness/codex/journal.py",
            "domain/agent/harness/codex/runner.py",
            "domain/agent/harness/codex/tools.py",
            "domain/agent/harness/codex/entry.py",
            "domain/agent/harness/codex/host.py",
        ),
        extra={"app/domain/agent/harness/codex/cheese.py": PLATFORM_TOOLS.read_text()},
    )
