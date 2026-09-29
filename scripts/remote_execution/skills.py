"""A repository's skills work in a room as they do in plain Claude Code.

A hosted repository keeps its skills in `.claude/skills`, and nothing about
being hosted may ask it to keep them anywhere else. So this plays one skill
scenario twice, against the same deterministic model (`model_fixture.py`,
which answers each turn from a `DO:` directive) and over the same project at
the same path, rebuilt identically before each run:

  reference  plain Claude Code in the project, with its default setting
             sources: what the repository's own people see.
  room       the session as a room holds it: the runner archive, `bootstrap`
             preparing it (`client.prepare`: its namespace, the forwarded
             project view over FUSE, the shell prefix), and `runtime.py`
             serving the project as the room's executor.

Each observation is what the model was offered or handed back, never a path
the session happens to print: a room's session names a skill's directory in
its own config dir, which its file tools and commands carry to the project's
`.claude/skills` (`docs/remote-execution.md`). The room must observe what the
reference observes. A difference is a bug in how a room carries skills, unless
the observation is in EXPECTED with the reason the room differs.

Needs Linux: the session's namespace, and FUSE for the view.

Usage:
    python3 skills.py --claude <binary> [--output <receipts dir>]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import acceptance  # noqa: E402
import headless_contract as contract  # noqa: E402
import runner_fixture  # noqa: E402
from headless_contract import DRIVER, Directives, do, is_  # noqa: E402
from model_fixture import Handler, Server  # noqa: E402

# Only on the machine that holds the project: a command that prints it ran
# there, and not on the session host.
MACHINE_MARK = "SKILL_FIXTURE_ON_THE_MACHINE"
# How long a change to the project's skills may take to reach the model. The
# reference build notices one through its file watcher within a few seconds.
NOTICE_TURNS = 6
NOTICE_PAUSE_S = 2.0

# Observations where the room knowingly differs, and why.
LINKED_ON_RETURN = (
    "plain Claude Code finds these in its own file tool, before the tool's "
    "result goes back; a room's file tools run on the executor, and the room "
    "links what they reached when they return, which the session notices "
    "within seconds or before the next turn (`release.touch_skills`)"
)
EXPECTED: dict[str, str] = {
    "nested skill offered in the turn that read its directory": LINKED_ON_RETURN,
    "path-scoped skill offered in the turn that wrote a matching file": (
        LINKED_ON_RETURN
    ),
}


def skill(directory, name, description, body, extra=""):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\n{body}\n"
    )


def project(path):
    """The repository: skills of each kind a repository keeps."""
    path.mkdir(parents=True)
    (path / "target.txt").write_text("TARGET\n")
    skills = path / ".claude/skills"
    skill(
        skills / "greet",
        "greet",
        "Greets the reader. GREET_LISTED.",
        "GREET_BODY. The details are in reference.md beside this file.",
    )
    (skills / "greet/reference.md").write_text("GREET_REFERENCE_BODY\n")
    skill(
        skills / "with-script",
        "with-script",
        "Runs the bundled script. SCRIPT_LISTED.",
        "Run ${CLAUDE_SKILL_DIR}/scripts/hello.sh\n"
        f'Machine: !`printf "%s" "${MACHINE_MARK}"`\n'
        "Directory: !`pwd`",
        "allowed-tools: Bash\n",
    )
    script = skills / "with-script/scripts/hello.sh"
    script.parent.mkdir()
    script.write_text(f'#!/bin/sh\nprintf "HELLO_FROM_SCRIPT %s\\n" "${MACHINE_MARK}"\n')
    script.chmod(0o755)
    skill(
        skills / "user-only",
        "user-only",
        "Only a person starts this. USER_ONLY_LISTED.",
        "USER_ONLY_BODY arguments=$ARGUMENTS",
        "disable-model-invocation: true\n",
    )
    (path / ".claude/commands").mkdir()
    (path / ".claude/commands/legacy.md").write_text(
        "---\ndescription: A command file. LEGACY_LISTED.\n---\nLEGACY_BODY\n"
    )
    (path / "pkg").mkdir()
    (path / "pkg/file.txt").write_text("PKG_FILE\n")
    skill(
        path / "pkg/.claude/skills/nested",
        "nested",
        "Belongs to the package. NESTED_LISTED.",
        "NESTED_BODY. The details are in reference.md beside this file.",
    )
    (path / "pkg/.claude/skills/nested/reference.md").write_text(
        "NESTED_REFERENCE_BODY\n"
    )
    # A name the root and a subdirectory both use.
    skill(skills / "deploy", "deploy", "Deploys. ROOTDEPLOY_LISTED.", "ROOTDEPLOY_BODY")
    skill(
        path / "pkg/.claude/skills/deploy",
        "deploy",
        "Deploys the package. PKGDEPLOY_LISTED.",
        "PKGDEPLOY_BODY",
    )
    # A dependency's skills, in a directory git ignores.
    (path / ".gitignore").write_text("vendored/\n")
    (path / "vendored/dep").mkdir(parents=True)
    (path / "vendored/dep/file.txt").write_text("DEP_FILE\n")
    skill(
        path / "vendored/dep/.claude/skills/dep",
        "dep",
        "A dependency's. IGNOREDDEP_LISTED.",
        "IGNOREDDEP_BODY",
    )
    # Skills for some files only.
    (path / "src").mkdir()
    (path / "src/a.py").write_text("SRC_FILE\n")
    skill(
        skills / "scoped",
        "scoped",
        "For the sources. PATHSCOPED_LISTED.",
        "PATHSCOPED_BODY",
        "paths: src/**\n",
    )
    skill(
        skills / "outputs",
        "outputs",
        "For what is written out. WRITESCOPED_LISTED.",
        "WRITESCOPED_BODY",
        "paths:\n  - \"out/**/*.txt\"\n",
    )
    git = ["git", "-c", "user.name=fixture", "-c", "user.email=f@example.invalid"]
    git += ["-c", "maintenance.auto=false"]
    env = dict(
        os.environ,
        GIT_AUTHOR_DATE="2026-01-01T00:00:00Z",
        GIT_COMMITTER_DATE="2026-01-01T00:00:00Z",
    )
    subprocess.run([*git, "init", "-q"], cwd=path, check=True, env=env)
    subprocess.run([*git, "add", "."], cwd=path, check=True, env=env)
    subprocess.run([*git, "commit", "-qm", "base"], cwd=path, check=True, env=env)


class Layout:
    """The paths both runs use, laid out afresh and identically."""

    def __init__(self, root):
        self.root = root
        self.machine = root / "machine"
        self.home = self.machine / "home"
        # With a space, as a machine's path can have.
        self.project = self.machine / "the project"
        self.template = root / "template"
        project(self.template)

    def rebuild(self):
        shutil.rmtree(self.machine, ignore_errors=True)
        (self.home / ".claude").mkdir(parents=True)
        shutil.copytree(self.template, self.project, symlinks=True)
        shipped(self.home / ".claude")


def shipped(config):
    """A skill in the config dir rather than the project, with a file beside
    it: a personal skill to plain Claude Code, and to a room one the platform
    ships (its own, or a way of working the project saved), which the launch
    writes into the session's config dir and the executor's alike."""
    skill(
        config / "skills/shipped",
        "shipped",
        "Shipped with the config. SHIPPED_LISTED.",
        "SHIPPED_BODY. The details are in reference.md beside this file.",
    )
    (config / "skills/shipped/reference.md").write_text("SHIPPED_REFERENCE_BODY\n")


def model_server(folder):
    server = Server(("127.0.0.1", 0), Handler)
    server.state = {"dir": folder, "actions": Directives(), "requests": []}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Reference:
    """Plain Claude Code, started in the project."""

    name = "reference"

    def __init__(self, binary, layout, folder):
        layout.rebuild()
        env = contract.fixture_env(layout.home, folder, 0)
        env.pop("ANTHROPIC_BASE_URL")
        env[MACHINE_MARK] = MACHINE_MARK
        self.session = contract.Session(
            binary,
            folder,
            "reference",
            DRIVER,
            launch={
                "command": [
                    binary,
                    "--strict-mcp-config",
                    "--mcp-config",
                    json.dumps({"mcpServers": {}}),
                    "--no-chrome",
                ],
                "env": env,
                "cwd": str(layout.project),
            },
            home=layout.home,
        )
        self.session.control({"subtype": "initialize"})

    def say(self, text, timeout=120):
        mark = self.session.user(text)
        _, result = self.session.wait(is_("result"), timeout, mark)
        assert result is not None, f"no result for {text!r}"
        return result

    def requests(self):
        return self.session.requests()

    def close(self):
        self.session.stop()


class Room:
    """The session a room runs: the runner, `bootstrap` and the executor."""

    name = "room"

    def __init__(self, binary, layout, folder):
        layout.rebuild()
        self.folder = folder
        self.server = model_server(folder)
        base = f"http://127.0.0.1:{self.server.server_port}"
        programs = folder / "executor"
        runtime = acceptance.executor_release.install(programs)
        self.target = {
            "command": [sys.executable, str(runtime)],
            "state": str(programs / "state"),
            "mcp_servers": [],
        }
        self.executor = acceptance.client.RemoteClient(self.target)
        # The executor's own processes, `claude mcp serve` among them, start
        # with the machine's HOME and environment.
        machine_env = dict(
            os.environ,
            HOME=str(layout.home),
            CLAUDE_CONFIG_DIR=str(layout.home / ".claude"),
        )
        acceptance.run(
            self.executor.command("start"),
            input=json.dumps(
                {
                    "workspace": str(layout.project),
                    "claude": binary,
                    "env": {MACHINE_MARK: MACHINE_MARK},
                    "mcp_servers": {},
                }
            ),
            env=machine_env,
        )
        home = folder / "session-home"
        home.mkdir()
        env = {
            key: os.environ[key]
            for key in ("PATH", "LANG", "TMPDIR", "SHELL")
            if key in os.environ
        }
        env.update(
            ANTHROPIC_BASE_URL=base,
            ANTHROPIC_AUTH_TOKEN="fixture-no-real-credential",
            DISABLE_TELEMETRY="1",
            DISABLE_ERROR_REPORTING="1",
            DISABLE_AUTOUPDATER="1",
            CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
            CHEESE_API=base,
            CHEESE_TOPIC="fixture",
            CHEESE_TOKEN="fixture-place-token",
        )
        env.update(runner_fixture.room_home(home, self.target))
        shipped(Path(env["CLAUDE_CONFIG_DIR"]))
        command = runner_fixture.room_command(
            home, binary, ["--model", contract.MODEL]
        )
        self.session = runner_fixture.Session.start(folder, command, env, home)

    def say(self, text, timeout=120):
        return self.session.turn(text, timeout)

    def requests(self):
        return self.server.state["requests"]

    def close(self):
        self.session.stop(self.folder / "journal.jsonl")
        subprocess.run(self.executor.command("stop"), capture_output=True, timeout=30)
        self.server.shutdown()
        self.server.server_close()


# --- what the model saw -------------------------------------------------------


def text_since(run, mark):
    return json.dumps(run.requests()[mark:], ensure_ascii=False)


def listing(run):
    """Every skill and command the latest request offered the model."""
    return json.dumps(run.requests()[-1], ensure_ascii=False)


def listed(run, *markers):
    offered = listing(run)
    return {marker: marker in offered for marker in markers}


def results(run, mark):
    """The tool results the model was handed since `mark`."""
    found = []
    for request in run.requests()[mark:]:
        for block in contract.last_tool_results(request):
            found.append(contract.text_of(block))
    return found


def skill_directory(run, mark):
    """The directory the session last told the model a skill it loaded is in."""
    found = re.findall(
        r"Base directory for this skill: ([^\\\n\"]+)", text_since(run, mark)
    )
    return found[-1] if found else None


def turn(run, text):
    mark = len(run.requests())
    run.say(text)
    return mark


def until_offered(run, marker, present=True):
    """Turns, a pause apart, until the model is offered `marker` (or no longer
    is); how many it took, or None."""
    for attempt in range(1, NOTICE_TURNS + 1):
        time.sleep(NOTICE_PAUSE_S)
        turn(run, f"anything new? {attempt}")
        if (marker in listing(run)) == present:
            return attempt
    return None


# --- the scenario ---------------------------------------------------------------


def scenario(run, layout):
    seen = {}
    turn(run, "hello")
    seen["listed at start"] = listed(
        run,
        "GREET_LISTED",
        "SCRIPT_LISTED",
        "USER_ONLY_LISTED",
        "LEGACY_LISTED",
        "NESTED_LISTED",
        "ROOTDEPLOY_LISTED",
        "PKGDEPLOY_LISTED",
        "PATHSCOPED_LISTED",
        "WRITESCOPED_LISTED",
    )

    mark = turn(run, do("Skill", skill="greet"))
    seen["skill body"] = "GREET_BODY" in text_since(run, mark)
    directory = skill_directory(run, mark)
    mark = turn(run, do("Read", file_path=f"{directory}/reference.md"))
    seen["supporting file"] = any("GREET_REFERENCE_BODY" in r for r in results(run, mark))

    mark = turn(run, do("Skill", skill="shipped"))
    seen["config dir skill body"] = "SHIPPED_BODY" in text_since(run, mark)
    directory = skill_directory(run, mark)
    mark = turn(run, do("Read", file_path=f"{directory}/reference.md"))
    seen["config dir skill supporting file"] = any(
        "SHIPPED_REFERENCE_BODY" in r for r in results(run, mark)
    )

    mark = turn(run, do("Skill", skill="with-script"))
    loaded = text_since(run, mark)
    seen["dynamic context ran on the machine"] = f"Machine: {MACHINE_MARK}" in loaded
    seen["dynamic context directory"] = f"Directory: {layout.project}" in loaded
    directory = skill_directory(run, mark)
    mark = turn(
        run, do("Bash", command=f'"{directory}/scripts/hello.sh"', description="run")
    )
    seen["script ran on the machine"] = any(
        f"HELLO_FROM_SCRIPT {MACHINE_MARK}" in r for r in results(run, mark)
    )

    mark = turn(run, do("Skill", skill="user-only"))
    seen["model refused a user-only skill"] = any(
        "disable-model-invocation" in r for r in results(run, mark)
    )
    mark = turn(run, "/user-only FIRST_ARGUMENT")
    seen["a person starts a user-only skill"] = (
        "USER_ONLY_BODY arguments=FIRST_ARGUMENT" in text_since(run, mark)
    )
    mark = turn(run, "/legacy")
    seen["a person starts a command file"] = "LEGACY_BODY" in text_since(run, mark)

    turn(run, do("Read", file_path=str(layout.project / "pkg/file.txt")))
    seen["nested skill offered in the turn that read its directory"] = (
        "NESTED_LISTED" in listing(run)
    )
    turn(run, "after reading in the package")
    offered = listing(run)
    seen["nested skill offered once its directory is read"] = "NESTED_LISTED" in offered
    seen["nested skill described with its directory"] = (
        "NESTED_LISTED. (from pkg/.claude/skills \u2014 applies when working on "
        "files under pkg/)" in offered
    )
    seen["nested skill sharing a root skill's name described as scoped"] = (
        "PKGDEPLOY_LISTED. (scoped to pkg/ \u2014 use this instead of the unscoped "
        '\\"deploy\\" skill' in offered
    )
    mark = turn(run, do("Skill", skill="nested"))
    seen["nested skill body"] = "NESTED_BODY" in text_since(run, mark)
    directory = skill_directory(run, mark)
    mark = turn(run, do("Read", file_path=f"{directory}/reference.md"))
    seen["nested skill supporting file"] = any(
        "NESTED_REFERENCE_BODY" in r for r in results(run, mark)
    )
    mark = turn(run, do("Skill", skill="deploy"))
    loaded = text_since(run, mark)
    seen["the root skill keeps a shared name"] = (
        "ROOTDEPLOY_BODY" in loaded and "PKGDEPLOY_BODY" not in loaded
    )
    mark = turn(run, do("Skill", skill="pkg:deploy"))
    seen["the nested skill of a shared name runs as dir:name"] = (
        "PKGDEPLOY_BODY" in text_since(run, mark)
    )

    turn(run, do("Read", file_path=str(layout.project / "vendored/dep/file.txt")))
    turn(run, "after reading in the dependency")
    seen["a gitignored directory's skill offered"] = "IGNOREDDEP_LISTED" in listing(run)

    turn(run, do("Read", file_path=str(layout.project / "src/a.py")))
    seen["path-scoped skill offered in the turn that read a matching file"] = (
        "PATHSCOPED_LISTED" in listing(run)
    )
    turn(run, "after reading a source")
    seen["path-scoped skill offered once a matching file is read"] = (
        "PATHSCOPED_LISTED" in listing(run)
    )
    mark = turn(run, do("Skill", skill="scoped"))
    seen["path-scoped skill body"] = "PATHSCOPED_BODY" in text_since(run, mark)
    turn(
        run,
        do("Write", file_path=str(layout.project / "out/deep/new.txt"), content="NEW"),
    )
    seen["path-scoped skill offered in the turn that wrote a matching file"] = (
        "WRITESCOPED_LISTED" in listing(run)
    )
    turn(run, "after writing an output")
    seen["path-scoped skill offered once a matching file is written"] = (
        "WRITESCOPED_LISTED" in listing(run)
    )
    mark = turn(run, do("Skill", skill="outputs"))
    seen["path-scoped skill written for body"] = "WRITESCOPED_BODY" in text_since(
        run, mark
    )

    # Changed on the machine while the session runs: a pull, an edit in
    # another tool, another session's commit.
    skill(
        layout.project / ".claude/skills/late",
        "late",
        "Added while the session runs. LATE_LISTED.",
        "LATE_BODY",
    )
    seen["added skill offered"] = until_offered(run, "LATE_LISTED") is not None
    mark = turn(run, do("Skill", skill="late"))
    seen["added skill loads"] = "LATE_BODY" in text_since(run, mark)

    # Longer than it was, so a view that kept the old size would cut it short.
    skill(
        layout.project / ".claude/skills/greet",
        "greet",
        "Greets the reader. GREET_EDITED_LISTED.",
        "GREET_EDITED_BODY " + "long " * 200 + "GREET_EDITED_END",
    )
    seen["edited description offered"] = (
        until_offered(run, "GREET_EDITED_LISTED") is not None
    )
    mark = turn(run, do("Skill", skill="greet"))
    loaded = text_since(run, mark)
    seen["edited skill loads whole"] = (
        "GREET_EDITED_BODY" in loaded and "GREET_EDITED_END" in loaded
    )

    shutil.rmtree(layout.project / ".claude/skills/late")
    seen["removed skill no longer offered"] = (
        until_offered(run, "LATE_LISTED", present=False) is not None
    )
    mark = turn(run, do("Skill", skill="late"))
    seen["removed skill no longer loads"] = "LATE_BODY" not in text_since(run, mark)

    # Written by the session itself, as an agent asked to write a skill does.
    turn(
        run,
        do(
            "Write",
            file_path=str(layout.project / ".claude/skills/made/SKILL.md"),
            content="---\nname: made\ndescription: Written by the agent. "
            "MADE_LISTED.\n---\nMADE_BODY\n",
        ),
    )
    seen["skill the session wrote offered"] = (
        until_offered(run, "MADE_LISTED") is not None
    )
    mark = turn(run, do("Skill", skill="made"))
    seen["skill the session wrote loads"] = "MADE_BODY" in text_since(run, mark)
    return seen


def play(build, binary, layout, folder):
    folder.mkdir(parents=True)
    run = build(binary, layout, folder)
    try:
        return scenario(run, layout), None
    except Exception as error:  # noqa: BLE001 — a failed run is a result
        return {}, f"{type(error).__name__}: {error}"
    finally:
        run.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--reference-only",
        action="store_true",
        help="play only the reference, which needs neither Linux nor FUSE",
    )
    arguments = parser.parse_args()
    binary = str(Path(arguments.claude).resolve())
    root = Path(os.path.realpath(tempfile.mkdtemp(prefix="skills-")))
    layout = Layout(root)
    try:
        reference, reference_error = play(Reference, binary, layout, root / "reference")
        room, room_error = (
            (dict(reference), None)
            if arguments.reference_only
            else play(Room, binary, layout, root / "room")
        )
        rows = []
        for name in reference.keys() | room.keys():
            left, right = reference.get(name), room.get(name)
            rows.append(
                {
                    "observation": name,
                    "reference": left,
                    "room": right,
                    "equal": left == right,
                    "expected difference": EXPECTED.get(name),
                }
            )
        rows.sort(key=lambda row: list(reference).index(row["observation"])
                  if row["observation"] in reference else len(reference))
        receipt = {
            "claude": binary,
            "version": subprocess.run(
                [binary, "--version"], capture_output=True, text=True
            ).stdout.strip(),
            "errors": {"reference": reference_error, "room": room_error},
            "observations": rows,
        }
        failed = bool(reference_error or room_error) or any(
            not row["equal"] and not row["expected difference"] for row in rows
        )
        stale = [
            row["observation"]
            for row in rows
            if row["equal"] and row["expected difference"]
        ]
        for row in rows:
            verdict = (
                "PASS"
                if row["equal"]
                else "KNOWN"
                if row["expected difference"]
                else "FAIL"
            )
            print(
                f"{verdict:10} {row['observation']}: reference={json.dumps(row['reference'])}"
                f" room={json.dumps(row['room'])}"
                + (f" ({row['expected difference']})" if not row["equal"] and row["expected difference"] else ""),
                flush=True,
            )
        for side, error in receipt["errors"].items():
            if error:
                print(f"ERROR      {side}: {error}", flush=True)
        for name in stale:
            print(f"STALE      {name} matches now; take it out of EXPECTED", flush=True)
        if arguments.output:
            arguments.output.mkdir(parents=True, exist_ok=True)
            (arguments.output / "skills.json").write_text(
                json.dumps(receipt, indent=2, ensure_ascii=False)
            )
            for side in ("reference", "room"):
                source = root / side
                if source.exists():
                    shutil.copytree(
                        source,
                        arguments.output / side,
                        ignore=shutil.ignore_patterns("*.pyz", "executor", "forwarded-project"),
                        dirs_exist_ok=True,
                    )
        return 1 if failed or stale else 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
