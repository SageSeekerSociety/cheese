"""The Claude Code runner archive the session machine runs."""

from app.domain.agent.harness.driven import bundle


def build() -> bytes:
    return bundle.build(
        "app.domain.agent.harness.claude_code.entry",
        (
            "domain/agent/harness/claude_code/journal.py",
            "domain/agent/harness/claude_code/runner.py",
            "domain/agent/harness/claude_code/entry.py",
        ),
    )
