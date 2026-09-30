"""Negative control: omit only durable echo association, without editing source."""

import pytest

from app.domain.agent.harness.claude_code.journal import Journal

remember = Journal.remember


def omit_echo_identity(self, key, value):
    if not key.startswith("receipt:"):
        remember(self, key, value)


Journal.remember = omit_echo_identity
raise SystemExit(
    pytest.main(
        [
            "-q",
            "tests/unit/test_native_echo_recovery.py",
            "-k",
            "replacement_preserves",
        ]
    )
)
