"""The CLI a sandbox runs must be the one THIS backend shipped.

Observed 2026-08-10 on the dev box: containers ran a `cheese` whose
`remember`/`recall` still omitted the `topic` field, so every memory an agent
wrote went to the wrong pool — silently, for as long as the box had been up.
The mount source was an operator-maintained host checkout nothing kept in sync.

The fix makes freshness structural rather than procedural: the backend stages
its own copy into each topic's session dir on every turn, and a screen takes the
CLI from THERE. These tests pin the staging half of that chain — a fresh copy on
every turn, and a stale one replaced.
"""

import uuid
from pathlib import Path

import pytest

from app.domain.workspace import service as ws

_CLI_SRC = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


@pytest.fixture
def session(monkeypatch, tmp_path):
    monkeypatch.setattr(ws.settings, "workspace_root", str(tmp_path))
    return ws.session_dir(uuid.uuid4(), uuid.uuid4())


def test_the_session_dir_carries_this_builds_cli(session):
    staged = ws.cheese_cli_mount_source(session)
    assert staged.is_file()
    assert staged.read_bytes() == _CLI_SRC.read_bytes()


def test_the_staged_cli_is_executable_by_the_container(session):
    """The loosen walk sets every session file to 0o666; the CLI is mounted at
    /usr/local/bin/cheese and has to actually run."""
    staged = ws.cheese_cli_mount_source(session)
    assert staged.stat().st_mode & 0o111, oct(staged.stat().st_mode)


def test_a_stale_staged_copy_is_refreshed_on_the_next_turn(monkeypatch, tmp_path):
    """A topic whose container has been alive for weeks still gets the CLI of
    whatever backend is running now."""
    monkeypatch.setattr(ws.settings, "workspace_root", str(tmp_path))
    project_id, topic_id = uuid.uuid4(), uuid.uuid4()
    staged = ws.cheese_cli_mount_source(ws.session_dir(project_id, topic_id))
    staged.write_text("#!/bin/sh\necho a CLI from three deploys ago\n")

    ws.session_dir(project_id, topic_id)  # next turn

    assert staged.read_bytes() == _CLI_SRC.read_bytes()
