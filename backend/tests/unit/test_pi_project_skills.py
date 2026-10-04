"""A project's own skills reach a pi room as they reach pi opened in the project.

pi 1.0.0, run in a project it trusts, loads `.pi/skills` and the plain paths
in `.pi/settings.json` from its working directory, and `.agents/skills` from
the working directory up to the git root; it never reads `.claude/skills`, and
of two skills with one name it keeps the first. A room starts pi with
`--no-skills`, so what it loads is exactly what the runner names with `--skill`.
These read that argv the way pi reads it — a directory holding SKILL.md is one
skill, a file is one skill, the first of a name wins — and compare the result
with what plain pi offers for the same project (checked against the real 1.0.0
binary, and against 0.85.1 before it).
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.domain.agent.harness.driven.runner import SessionStart
from app.domain.agent.harness.pi.runner import Runner
from tests.support.room_machine import room_machine

FAKE = Path(__file__).resolve().parents[1] / "support/fake_pi.py"
FIXTURE = Path(__file__).parent / "fixtures/pi-entries.json"


def skill(directory: Path, name: str, description: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n"
    )


def loose(path: Path, name: str, description: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\nbody\n")


def project(root: Path) -> Path:
    """A repository with skills in every place pi looks, and some it does not."""
    skill(root / ".agents/skills/above", "above", "above the repository")
    repo = root / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    skill(repo / ".pi/skills/a", "a", "pi skills directory")
    loose(repo / ".pi/skills/loose.md", "loose", "loose file at the top of .pi/skills")
    (repo / ".pi/settings.json").write_text(json.dumps({"skills": ["../extra"]}))
    skill(repo / "extra/e", "e", "named in the project settings")
    skill(repo / ".agents/skills/b", "b", "agents skills at the root")
    loose(repo / ".agents/skills/top.md", "top", "loose file at the top of .agents")
    loose(
        repo / ".agents/skills/group/g.md", "g", "loose file below the top of .agents"
    )
    skill(repo / ".agents/skills/documents", "documents", "the repository's own")
    skill(repo / ".claude/skills/c", "c", "claude skills directory")
    skill(repo / "pkg/.agents/skills/n", "n", "agents skills in the package")
    return repo


PLATFORM = {
    "skills/documents/SKILL.md": (
        "---\nname: documents\ndescription: the platform's own\n---\nbody\n"
    )
}


def offered(argv: list[str]) -> dict[str, str]:
    """name → description of every skill pi loads from these `--skill` paths."""
    found: dict[str, str] = {}
    for i, item in enumerate(argv):
        if item != "--skill":
            continue
        path = Path(argv[i + 1])
        file = path / "SKILL.md" if path.is_dir() else path
        header = file.read_text().split("---")[1]
        fields = dict(
            line.split(": ", 1) for line in header.strip().splitlines() if ": " in line
        )
        found.setdefault(fields["name"], fields["description"])
    return found


async def started(tmp_path: Path, cwd: Path) -> dict[str, str]:
    binary = tmp_path / "pi"
    binary.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} {FIXTURE} "$@"\n')
    binary.chmod(0o700)
    recorded = tmp_path / "argv.json"
    runner = Runner(tmp_path / "state")
    here = tmp_path / "session-host"
    here.mkdir()
    # The checkout is on the room's machine; pi loads what the runner copied.
    with room_machine(tmp_path / "machine", checkout=cwd) as target:
        try:
            await runner.start(
                SessionStart("system prompt", None, agent_handle="teammate"),
                binary=str(binary),
                cwd=str(here),
                env={"PATH": "/usr/bin:/bin", "PI_FAKE_ARGV": str(recorded)},
                args=["--no-skills"],
                target=target,
                skills=PLATFORM,
            )
        finally:
            await runner.close()
    return offered(json.loads(recorded.read_text()))


@pytest.mark.anyio
async def test_a_room_at_the_root_offers_what_pi_offers_there(tmp_path):
    repo = project(tmp_path / "work")
    assert await started(tmp_path, repo) == {
        "e": "named in the project settings",
        "a": "pi skills directory",
        "loose": "loose file at the top of .pi/skills",
        "b": "agents skills at the root",
        "g": "loose file below the top of .agents",
        # The project's skill wins a shared name, as it does in plain pi.
        "documents": "the repository's own",
    }


@pytest.mark.anyio
async def test_a_room_below_the_root_offers_what_pi_offers_there(tmp_path):
    """`.pi` is read from the working directory only; `.agents/skills` from it
    up to the git root, and not above."""
    repo = project(tmp_path / "work")
    assert await started(tmp_path, repo / "pkg") == {
        "n": "agents skills in the package",
        "b": "agents skills at the root",
        "g": "loose file below the top of .agents",
        "documents": "the repository's own",
    }


@pytest.mark.anyio
async def test_the_owners_own_skills_stay_out_of_a_project_outside_git(
    tmp_path, monkeypatch
):
    """Outside a repository pi walks to the filesystem root, passing the home
    directory, whose `.agents/skills` is the user's own and not the project's."""
    home = tmp_path / "owner"
    monkeypatch.setenv("HOME", str(home))
    skill(home / ".agents/skills/mine", "mine", "the owner's own")
    work = home / "plain"
    skill(work / ".agents/skills/p", "p", "the project's")
    names = await started(tmp_path, work)
    assert names["p"] == "the project's"
    assert "mine" not in names
