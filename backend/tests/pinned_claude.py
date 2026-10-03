"""The pinned harness builds, for tests that run one.

An executor serves its file tools through the pinned Claude Code build's `mcp
serve`, and its commands start from the shell snapshot that build writes; the
Codex and pi runner tests drive those builds directly. Each comes only from its
CHEESE_TEST_* variable, never from PATH: on a developer machine the `claude` on
PATH is often a wrapper or another version, and a test run against it fails, or
passes, for reasons unrelated to the code under test.
"""

import functools
import os
import subprocess

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


@functools.cache
def pi_binary() -> str:
    """The pinned pi, and only it: the variable outlives the pin it was
    exported for, and another pi says other things to the model (an older one
    names its working directory in the system prompt), so a test would fail,
    or pass, for reasons that are not the code's."""
    from app.domain.agent.harness.pi.launch import VERSION

    binary = _pinned("CHEESE_TEST_PI", "pi")
    found = subprocess.run(
        [binary, "--version"], capture_output=True, text=True, timeout=60
    ).stdout.strip()
    if found != VERSION:
        pytest.fail(
            f"CHEESE_TEST_PI is pi {found or '(no version)'}, not the pinned"
            f" {VERSION}; run `{SETUP}` from the repository root again.",
            pytrace=False,
        )
    return binary
