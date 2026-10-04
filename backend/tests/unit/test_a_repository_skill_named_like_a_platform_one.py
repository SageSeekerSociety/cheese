"""A repository skill named like one the platform ships does not stop a room.

A room's session keeps the platform's skills (and the project's saved ways of
working) in its config dir, and exposes the repository's `.claude/skills`
there too, as links into the forwarded project view. A repository cannot know
which names the platform took, so one of its skills may share a name with
them. Plain Claude Code, given a personal skill and a project skill of the
same name, uses the personal one and carries on; a room must carry on too,
with the skill the platform shipped in that name.
"""

import os
from pathlib import Path

from app.domain.agent.harness.claude_code.remote_execution import release
from tests.unit.test_device_launch import _launch, _machine, _seed_skill_cache


def _tree(*names):
    entries = {}
    for name in names:
        parts = Path(name).parts
        for depth in range(1, len(parts)):
            entries["/".join(parts[:depth])] = {"kind": "directory"}
        entries[name] = {"kind": "file"}
    return {"generation": "one", "entries": entries}


def test_the_session_is_prepared_and_the_platform_skill_keeps_its_name(tmp_path):
    session, config, view = tmp_path / "session", tmp_path / "config", tmp_path / "view"
    session.mkdir()
    shipped = config / "skills/documents/SKILL.md"
    shipped.parent.mkdir(parents=True)
    shipped.write_text("THE PLATFORM'S\n")
    for name in ("documents", "deploy"):
        (view / ".claude/skills" / name).mkdir(parents=True)
        (view / ".claude/skills" / name / "SKILL.md").write_text("THE REPOSITORY'S\n")

    release.link_forwarded_user_context(
        session,
        config,
        view,
        _tree(
            ".claude/skills/documents/SKILL.md",
            ".claude/skills/deploy/SKILL.md",
        ),
        tmp_path / "helpers",
    )

    assert shipped.read_text() == "THE PLATFORM'S\n"
    assert (config / "skills/deploy/SKILL.md").read_text() == "THE REPOSITORY'S\n"


def test_a_skill_the_repository_no_longer_shadows_is_linked_again(tmp_path):
    """The repository's skill takes its name back once the platform stops
    shipping one by that name (a saved way of working deleted, say)."""
    session, config, view = tmp_path / "session", tmp_path / "config", tmp_path / "view"
    session.mkdir()
    shipped = config / "skills/deploy/SKILL.md"
    shipped.parent.mkdir(parents=True)
    shipped.write_text("SAVED WAY OF WORKING\n")
    (view / ".claude/skills/deploy").mkdir(parents=True)
    (view / ".claude/skills/deploy/SKILL.md").write_text("THE REPOSITORY'S\n")
    tree = _tree(".claude/skills/deploy/SKILL.md")
    release.link_forwarded_user_context(session, config, view, tree, tmp_path / "h")

    (config / "skills/deploy/SKILL.md").unlink()
    (config / "skills/deploy").rmdir()
    release.link_forwarded_user_context(session, config, view, tree, tmp_path / "h")

    assert (config / "skills/deploy/SKILL.md").read_text() == "THE REPOSITORY'S\n"


def test_a_launch_writes_its_skills_over_a_link_an_earlier_session_left(tmp_path):
    """An earlier session linked the repository's skill of this name into the
    config dir; the platform now ships one by that name. The launch writes the
    platform's in place of the link and never through it into the project."""
    owner, session, _work, _claude, env = _machine(tmp_path)
    _seed_skill_cache(owner)
    repository = tmp_path / "view/.claude/skills/cheese-docs"
    repository.mkdir(parents=True)
    (repository / "SKILL.md").write_text("THE REPOSITORY'S\n")
    repository.chmod(0o500)
    from app.domain.agent.place import seat_dir

    skills = Path(seat_dir(str(session))) / ".claude/skills"
    skills.mkdir(parents=True)
    (skills / "cheese-docs").symlink_to(repository, target_is_directory=True)
    try:
        result = _launch(tmp_path, env)
    finally:
        repository.chmod(0o700)

    assert result.returncode == 0, result.stderr
    assert not (skills / "cheese-docs").is_symlink()
    assert "name: cheese-docs" in (skills / "cheese-docs/SKILL.md").read_text()
    assert (repository / "SKILL.md").read_text() == "THE REPOSITORY'S\n"
    assert os.listdir(repository) == ["SKILL.md"]
