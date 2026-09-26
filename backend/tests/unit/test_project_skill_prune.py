"""A device that had a project skill loses it once the project deletes it;
the platform's own skills and a project that never had one are left alone."""

import subprocess

from app.domain.agent.harness.claude_code.device_launch import project_skill_prune


def _run(config, shipped):
    subprocess.run(
        ["bash", "-euc", project_skill_prune(shipped)],
        env={"CLAUDE_CONFIG_DIR": str(config), "PATH": "/usr/bin:/bin"},
        check=True,
    )


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
