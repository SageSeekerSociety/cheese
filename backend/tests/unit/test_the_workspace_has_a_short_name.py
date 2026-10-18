"""A session's working directory also goes by a fixed, short name.

A line runs one Bash command per step and the shell's directory is reset between
them, so a session that names its project by spelling the path out pays for the
platform's own home — UUIDs all the way down — in most of its commands. `$T` is
the same name in every session; each session's own value is the project that
session sees, so two sessions on one machine never mean each other's directory.
"""

from pathlib import Path

from app.domain.agent.harness.claude_code.remote_execution.client import (
    PINNED_VERSION,
    WORKING_DIRECTORY,
    prepare,
)
from app.domain.agent.harness.claude_code.remote_execution.shell_prefix import (
    FORWARDED_ENV,
)
from app.domain.agent.executor_transport import respell_working_directory
from app.domain.agent.harness.prompt import WORKING_DIRECTORY as TOLD, build_system_prompt


def _pinned(tmp_path):
    """A build that answers the one question `prepare` asks it — its version.

    The real pinned build is what an executor test needs; this file is about the
    environment the launch carries, and `prepare` runs the binary nowhere."""
    binary = tmp_path / "claude"
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


def _complaints(launch):
    """What is wrong with the short name this launch carries, if anything.

    The check the three cases below lean on, so that "a name that points at the
    wrong directory" and "no name at all" are both things that can go red
    rather than a claim in a comment."""
    short = (launch.get("env") or {}).get(WORKING_DIRECTORY)
    if not short:
        return ["the session has no short name for its working directory"]
    if short != launch["workspace"]:
        return [f"the short name points at {short!r}, not at {launch['workspace']!r}"]
    return []


def test_every_session_names_its_working_directory(tmp_path):
    launch = _session(tmp_path / "session")
    assert launch["env"][WORKING_DIRECTORY] == launch["workspace"]
    assert _complaints(launch) == []


def test_two_sessions_on_one_machine_name_their_own_directories(tmp_path):
    first = _session(tmp_path / "first", workspace="/project-one")
    second = _session(tmp_path / "second", workspace="/project-two")
    assert first["env"][WORKING_DIRECTORY] == "/project-one"
    assert second["env"][WORKING_DIRECTORY] == "/project-two"


def test_the_name_is_short_and_reaches_the_executor(tmp_path):
    # What makes it worth typing: a name this short is shorter than any path it
    # could stand for, and the shell prefix carries it to the command's machine
    # (`run_on_the_machine` forwards `FORWARDED_ENV` and nothing else).
    assert len(WORKING_DIRECTORY) <= 2
    assert WORKING_DIRECTORY in FORWARDED_ENV


def test_the_line_is_told_the_name(tmp_path):
    assert "$T" in TOLD
    assert TOLD in build_system_prompt("base", "", working_directory=True)
    # A harness whose commands are not given the name is not told about it.
    assert TOLD not in build_system_prompt("base", "")


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


def test_a_name_missing_or_pointing_elsewhere_is_caught(tmp_path):
    """Deliberate violations: the session's name is removed, then pointed at a
    directory that is not its working directory. Both must be caught — a check
    that only ever passes is not a check."""
    launch = _session(tmp_path / "session")
    assert _complaints(launch) == []

    without = {
        **launch,
        "env": {
            name: value
            for name, value in launch["env"].items()
            if name != WORKING_DIRECTORY
        },
    }
    assert _complaints(without) == [
        "the session has no short name for its working directory"
    ]

    elsewhere = {
        **launch,
        "env": {**launch["env"], WORKING_DIRECTORY: str(Path(tmp_path))},
    }
    assert _complaints(elsewhere) == [
        f"the short name points at {str(Path(tmp_path))!r}, "
        f"not at {launch['workspace']!r}"
    ]
