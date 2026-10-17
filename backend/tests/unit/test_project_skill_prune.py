"""A skill a machine got last time and is no longer shipped is removed there.

A project skill goes once the project deletes it, and a platform skill once the
platform stops shipping it. Neither list touches the other's folders, and a
project that never had a skill leaves no trace."""

import subprocess

import pytest

from app.domain.agent.harness.claude_code.device_launch import (
    platform_skill_prune,
    project_skill_prune,
)

PLATFORM = ["cheese", "cheese-docs", "documents"]


def _shell(config, script):
    subprocess.run(
        ["bash", "-euc", script],
        env={"CLAUDE_CONFIG_DIR": str(config), "PATH": "/usr/bin:/bin"},
        check=True,
    )


def _run(config, shipped):
    _shell(config, project_skill_prune(shipped, PLATFORM))


def test_a_deleted_project_skill_is_removed_and_the_rest_stay(tmp_path):
    skills = tmp_path / "skills"
    for name in ("weekly-report", "summary", "documents"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(name)
    _run(tmp_path, ["weekly-report", "summary"])

    _run(tmp_path, ["weekly-report"])

    assert (skills / "weekly-report" / "SKILL.md").is_file()
    assert not (skills / "summary").exists()
    assert (skills / "documents" / "SKILL.md").is_file()


def test_a_project_without_skills_leaves_no_trace(tmp_path):
    _run(tmp_path, [])
    assert not (tmp_path / "skills").exists()


def test_a_platform_skill_no_longer_shipped_is_removed(tmp_path):
    skills = tmp_path / "skills"
    for name in ("cheese", "documents", "old-guide", "weekly-report"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(name)
    _shell(tmp_path, platform_skill_prune(["cheese", "documents", "old-guide"], []))

    _shell(tmp_path, platform_skill_prune(["cheese", "documents"], ["weekly-report"]))

    assert not (skills / "old-guide").exists()
    assert (skills / "cheese" / "SKILL.md").is_file()
    assert (skills / "weekly-report" / "SKILL.md").is_file()


def test_a_retired_name_the_project_now_uses_stays(tmp_path):
    skill = tmp_path / "skills" / "chat-detail" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("the project's own")

    _shell(tmp_path, platform_skill_prune(PLATFORM, ["chat-detail"]))

    assert skill.read_text() == "the project's own"


def test_an_executor_drops_the_skills_it_is_no_longer_shipped(tmp_path):
    """The executor's copy follows the same lists (`remote_execution/bootstrap`)."""
    from app.domain.agent.harness.claude_code.remote_execution.bootstrap import (
        CONFIG_DIR,
        prune_platform_skills,
        prune_project_skills,
    )

    skills = tmp_path / CONFIG_DIR / "skills"
    for name in ("cheese", "chat-detail", "summary", "weekly-report"):
        (skills / name).mkdir(parents=True)
    prune_project_skills(tmp_path, ["summary", "weekly-report"], PLATFORM)

    # A machine with no platform list yet had the skills shipped before it.
    prune_platform_skills(tmp_path, PLATFORM, ["weekly-report"], ["chat-detail"])
    prune_project_skills(tmp_path, ["weekly-report"], PLATFORM)

    assert sorted(p.name for p in skills.iterdir() if p.is_dir()) == [
        "cheese",
        "weekly-report",
    ]


def test_a_prepare_that_changes_nothing_writes_nothing(tmp_path):
    """清单没变就不写：每次工具调用都会重新装一次，而机器可能一个字节都写不进去。

    A full disk and a filesystem out of inodes both answer a write the same
    way, and the room cannot run a single tool — `echo ok` included — while
    every call goes through a write that changes nothing. Say the machine
    cannot be written to at all and the call has to go through anyway.
    """
    import json

    from app.domain.agent.harness.claude_code.remote_execution.bootstrap import (
        CONFIG_DIR,
        PROJECT_SKILLS_MANIFEST,
        prune_project_skills,
    )

    prune_project_skills(tmp_path, ["summary"], PLATFORM)
    manifest = tmp_path / CONFIG_DIR / PROJECT_SKILLS_MANIFEST
    assert json.loads(manifest.read_text()) == ["summary"]

    skills = tmp_path / CONFIG_DIR / "skills"
    skills.chmod(0o500)  # nothing more may be written in there
    try:
        prune_project_skills(tmp_path, ["summary"], PLATFORM)
        # The list itself did change, so this one really has to write.
        with pytest.raises(OSError):
            prune_project_skills(tmp_path, ["summary", "weekly-report"], PLATFORM)
    finally:
        skills.chmod(0o700)

    assert json.loads(manifest.read_text()) == ["summary"]


def test_a_prepare_that_changes_nothing_writes_no_platform_list_either(tmp_path):
    """平台那份清单同理，而报告里的报错正是它（`...platform-skills.json.next`）。"""
    from app.domain.agent.harness.claude_code.remote_execution.bootstrap import (
        CONFIG_DIR,
        prune_platform_skills,
    )

    prune_platform_skills(tmp_path, PLATFORM, [], [])
    skills = tmp_path / CONFIG_DIR / "skills"
    skills.chmod(0o500)
    try:
        prune_platform_skills(tmp_path, PLATFORM, [], [])
        with pytest.raises(OSError):
            prune_platform_skills(tmp_path, [*PLATFORM, "weekly-report"], [], [])
    finally:
        skills.chmod(0o700)
