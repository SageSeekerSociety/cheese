"""The pinned harness builds, for tests that run one.

An executor serves its file tools through the pinned Claude Code build's `mcp
serve`, and its commands start from the shell snapshot that build writes; the
Codex and pi runner tests drive those builds directly. Each comes only from its
CHEESE_TEST_* variable, never from PATH: on a developer machine the `claude` on
PATH is often a wrapper or another version, and a test run against it fails, or
passes, for reasons unrelated to the code under test.
"""

import os

import pytest

SETUP = 'eval "$(bash .claude/scripts/dev-db.sh start)"'


def _pinned(variable: str, harness: str) -> str:
    binary = os.environ.get(variable)
    if not binary:
        pytest.fail(
            f"{variable} must point to the pinned {harness} build; run `{SETUP}`"
            " from the repository root, which installs it and exports it.",
            pytrace=False,
        )
    return binary


def claude_binary() -> str:
    return _pinned("CHEESE_TEST_CLAUDE", "Claude Code")


def codex_binary() -> str:
    return _pinned("CHEESE_TEST_CODEX", "Codex")


def pi_binary() -> str:
    return _pinned("CHEESE_TEST_PI", "pi")
