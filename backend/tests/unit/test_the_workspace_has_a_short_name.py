"""A session's working directory also goes by a fixed, short name.

A line runs one Bash command per step and the shell's directory is reset between
them, so a session that names its project by spelling the path out pays for the
platform's own home — UUIDs all the way down — in most of its commands. `$T` is
the same name in every session; each session's own value is the project that
session sees, so two sessions on one machine never mean each other's directory.
"""

from app.domain.agent.executor_transport import respell_working_directory
from app.domain.agent.harness import CLAUDE_CODE, CODEX
from app.domain.agent.harness.claude_code.remote_execution.client import (
    PINNED_VERSION,
    WORKING_DIRECTORY,
    prepare,
)
from app.domain.agent.harness.claude_code.remote_execution.shell_prefix import (
    FORWARDED_ENV,
)
from app.domain.agent.harness.prompt import WORKING_DIRECTORY as TOLD
from app.domain.agent.room.system_prompt import session_system_prompt


def _pinned(directory):
    """A build that answers the one question `prepare` asks it — its version.

    The real pinned build is what an executor test needs; this file is about the
    environment the launch carries, and `prepare` runs the binary nowhere."""
    binary = directory / "claude"
    binary.write_text(f"#!/bin/sh\necho '{PINNED_VERSION}'\n")
    binary.chmod(0o755)
    return str(binary)


def _session(directory, workspace="/unavailable-project"):
    directory.mkdir(parents=True, exist_ok=True)
    return prepare(
        directory,
        {"kind": "unavailable", "workspace": workspace},
        claude=_pinned(directory),
    )


def _room_prompt(harness):
    return session_system_prompt(
        "base",
        "",
        needs_place=True,
        has_doc=False,
        role=None,
        harness=harness,
        name="cheese",
    )


def test_every_session_names_its_working_directory(tmp_path):
    launch = _session(tmp_path / "session")
    assert launch["env"][WORKING_DIRECTORY] == launch["workspace"]


def test_two_sessions_on_one_machine_name_their_own_directories(tmp_path):
    first = _session(tmp_path / "first", workspace="/project-one")
    second = _session(tmp_path / "second", workspace="/project-two")
    assert first["env"][WORKING_DIRECTORY] == "/project-one"
    assert second["env"][WORKING_DIRECTORY] == "/project-two"


def test_the_name_is_short_and_reaches_the_executor():
    # What makes it worth typing: a name this short is shorter than any path it
    # could stand for, and the shell prefix carries it to the command's machine
    # (`run_on_the_machine` forwards `FORWARDED_ENV` and nothing else).
    assert len(WORKING_DIRECTORY) <= 2
    assert WORKING_DIRECTORY in FORWARDED_ENV


def test_the_line_is_told_the_name_only_on_the_harness_that_has_it():
    """The room's own entry point, not `build_system_prompt` directly: the
    harness gate lives in `session_system_prompt`, and a test that skipped it
    would stay green with the gate removed."""
    assert "$T" in TOLD
    assert TOLD in _room_prompt(CLAUDE_CODE)
    # codex and pi are not given the variable, so they are not told about it.
    assert TOLD not in _room_prompt(CODEX)


def test_the_name_crosses_to_the_machine_spelled_the_machines_way():
    """A session started before its machine was rented sees the project at a
    placeholder while its commands already run on the machine. The name is a
    path like `cwd` and `body` are, so it is respelled the same way — or the
    command the line was told to write, `cd "$T"`, names nothing there."""
    placeholder, machine = "/unavailable-project", "/home/user/project"
    forwarded = respell_working_directory(
        {WORKING_DIRECTORY: placeholder, "PATH": "/usr/bin"}, placeholder, machine
    )
    assert forwarded[WORKING_DIRECTORY] == machine
    assert forwarded["PATH"] == "/usr/bin"


def test_a_name_already_spelled_the_machines_way_is_left_alone():
    machine = "/home/user/project"
    unchanged = respell_working_directory(
        {WORKING_DIRECTORY: machine}, machine, machine
    )
    assert unchanged[WORKING_DIRECTORY] == machine
    # Nothing to reply to, and nothing invented: an environment without the
    # name, or no environment at all, comes back as it went in.
    assert respell_working_directory({"PATH": "/usr/bin"}, machine, machine) == {
        "PATH": "/usr/bin"
    }
    assert respell_working_directory(None, machine, machine) is None
