"""The Codex runner archive sent to the session host."""

from app.domain.agent.harness.driven import bundle


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
    )
