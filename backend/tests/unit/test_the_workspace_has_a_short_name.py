"""A session's working directory also goes by a fixed, short name.

A line runs one Bash command per step and the shell's directory is reset between
them, so a session that names its project by spelling the path out pays for the
platform's own home — UUIDs all the way down — in most of its commands. `$T` is
the same name in every session; each session's own value is the project that
session sees, so two sessions on one machine never mean each other's directory.
"""

import shlex

from app.domain.agent.executor_transport import to_machine
from app.domain.agent.harness import CLAUDE_CODE, CODEX
from app.domain.agent.harness.claude_code.remote_execution.client import (
    PINNED_VERSION,
    WORKING_DIRECTORY,
    prepare,
)
from app.domain.agent.harness.claude_code.remote_execution.shell_prefix import (
    FORWARDED_ENV,
    bind_working_directory,
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


def test_the_name_is_short_and_reaches_the_command():
    # What makes it worth typing: a name this short is shorter than any path it
    # could stand for, and the shell prefix binds it in the command it runs on
    # the machine.
    assert len(WORKING_DIRECTORY) <= 2
    assert (
        bind_working_directory('cd "$T" && pwd', "/the project")
        == "T='/the project'\ncd \"$T\" && pwd"
    )


def test_the_machines_environment_does_not_carry_the_name():
    """A build running natively on the executor has no `T`, and a room's command
    must see exactly the environment that one sees — the equivalence check runs
    both and holds them equal. Forwarding the name as an environment variable
    put it in every command's `env` listing on one side only and turned the four
    equivalence shards red; it travels inside the command's body instead."""
    assert WORKING_DIRECTORY not in FORWARDED_ENV
    assert bind_working_directory("pwd", None) == "pwd"


def test_a_placeholder_is_quoted_for_the_substitution_that_spells_it():
    """A session started before its machine is rented names a placeholder, and
    the transport replaces it inside the body after this point, textually. The
    binding is single-quoted for that: the machine's path can have a space, and
    an escaped value would no longer match the placeholder it replaces."""
    bound = bind_working_directory(
        'cd "$T" && pwd', "/unavailable-project", spelled_later=True
    )
    spelled = to_machine(bound, "/unavailable-project", "/home/First Last/project")
    assert spelled.splitlines()[0] == "T='/home/First Last/project'"
    # A placeholder with a quote cannot be spelled that way: nothing is bound
    # rather than a value that would break the shell.
    assert (
        bind_working_directory("pwd", "/it's here/workspace", spelled_later=True)
        == "pwd"
    )


def test_a_path_with_spaces_and_quotes_is_bound_whole():
    # A machine's project path can have both; an unquoted binding would split it
    # and `cd "$T"` would name a part of it.
    path = "/the project/it's here"
    body = bind_working_directory("pwd", path)
    assert body.splitlines()[0] == "T=" + shlex.quote(path)
    assert shlex.split(body.splitlines()[0]) == ["T=" + path]


def test_the_line_is_told_the_name_only_on_the_harness_that_has_it():
    """The room's own entry point, not `build_system_prompt` directly: the
    harness gate lives in `session_system_prompt`, and a test that skipped it
    would stay green with the gate removed."""
    assert "$T" in TOLD
    assert TOLD in _room_prompt(CLAUDE_CODE)
    # codex and pi are not given the variable, so they are not told about it.
    assert TOLD not in _room_prompt(CODEX)
