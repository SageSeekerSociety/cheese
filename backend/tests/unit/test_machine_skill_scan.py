"""机器的技能扫描器：真跑一遍那段脚本，钉住它认哪些目录、报哪些问题。

扫描跑在用户机器上（`scan.program()` 就是整段脚本），所以这里用真的解释器
跑真的脚本，而不是复述它的逻辑——脚本里任何一处走样，这几条就会红。

不碰数据库：这一层只关心「机器说了什么」，`MachineSkillService.record_scan`
怎么把它落成四种 state 是另一回事。
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from app.domain.machine_skill import scan


def _skill(root: Path, name: str, *, description: str = "A skill.") -> Path:
    """A spec-shaped skill: a directory named ``name`` holding one SKILL.md."""
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nDo it.\n",
        encoding="utf-8",
    )
    return folder


def _run(home: Path, project: str | None = None, *, skip_homes: bool = False) -> dict:
    result = subprocess.run(
        [sys.executable, "-", project or "", "1" if skip_homes else ""],
        input=scan.program(),
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return scan.read(
        {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit": 0,
            "truncated": False,
        }
    )


def _land(home: Path, name: str, files: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-"],
        input=scan.land_program(name, files),
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
        check=False,
    )


def test_a_skill_is_found_and_its_digest_is_the_file_it_read(tmp_path):
    skill = _skill(tmp_path / ".claude" / "skills", "alpha")
    said = _run(tmp_path)
    assert [row["name"] for row in said["skills"]] == ["alpha"]
    row = said["skills"][0]
    assert row["source_root"] == str(skill)
    written = (skill / "SKILL.md").read_bytes()
    assert row["digest"] == hashlib.sha256(written).hexdigest()
    assert row["description"] == "A skill."


def test_the_nested_system_directory_is_reached(tmp_path):
    # ~/.codex/skills/.system/<name>/SKILL.md is depth two; the scan is depth three.
    _skill(tmp_path / ".codex" / "skills" / ".system", "ponytail")
    said = _run(tmp_path)
    assert [row["name"] for row in said["skills"]] == ["ponytail"]


def test_a_folded_description_is_read_as_one_line(tmp_path):
    folder = tmp_path / ".claude" / "skills" / "long"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: long\ndescription: >\n  first half\n  second half\n---\n\nBody.\n",
        encoding="utf-8",
    )
    said = _run(tmp_path)
    assert said["skills"][0]["description"] == "first half second half"


def test_a_directory_that_breaks_the_spec_is_a_problem_not_a_skill(tmp_path):
    root = tmp_path / ".claude" / "skills"
    # name must equal the directory it sits in.
    _skill(root, "beta", description="A skill.")
    (root / "beta" / "SKILL.md").write_text(
        "---\nname: gamma\ndescription: A skill.\n---\n\nBody.\n", encoding="utf-8"
    )
    # description is the one trigger and cannot be empty.
    (root / "nodesc").mkdir(parents=True)
    (root / "nodesc" / "SKILL.md").write_text(
        "---\nname: nodesc\n---\n\nBody.\n", encoding="utf-8"
    )
    said = _run(tmp_path)
    assert said["skills"] == []
    assert len(said["problems"]) == 2


def test_the_first_root_wins_a_name_two_roots_share(tmp_path):
    _skill(tmp_path / ".claude" / "skills", "shared", description="From claude.")
    _skill(tmp_path / ".trae" / "skills", "shared", description="From trae.")
    said = _run(tmp_path)
    assert len(said["skills"]) == 1
    assert said["skills"][0]["description"] == "From claude."


def test_what_an_installed_plugin_brought_is_found(tmp_path):
    # ~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/skills/<name>.
    _skill(
        tmp_path
        / ".claude"
        / "plugins"
        / "cache"
        / "market"
        / "tool"
        / "1.0"
        / "skills",
        "wrapped",
    )
    said = _run(tmp_path)
    assert [row["name"] for row in said["skills"]] == ["wrapped"]


def test_what_trae_ships_is_found_under_its_capitalised_name(tmp_path):
    _skill(tmp_path / ".trae-cn" / "builtin_skills", "TRAE-code-review")
    said = _run(tmp_path)
    assert [row["name"] for row in said["skills"]] == ["TRAE-code-review"]
    assert said["problems"] == []


def test_a_self_hosted_machine_leaves_its_personal_homes_out(tmp_path):
    # A machine the person enrolled themselves: the ~/.claude / ~/.trae skills
    # they wrote for their own agents are theirs, so the scan does not read the
    # homes at all — nothing of their private setup reaches the platform.
    _skill(tmp_path / ".claude" / "skills", "mine")
    _skill(tmp_path / ".trae-cn" / "builtin_skills", "TRAE-code-review")
    said = _run(tmp_path, skip_homes=True)
    assert said["skills"] == []
    assert said["roots"] == []


def test_a_self_hosted_machine_still_reads_what_the_platform_manages(tmp_path):
    # Only the homes go. The workspaces a person listed, a session's project
    # skills and what was landed from the internet stay readable.
    _skill(tmp_path / ".cheese" / "web-skills", "fetched")
    _skill(
        tmp_path / ".cheese" / "home" / "proj-a" / "gen1" / ".claude" / "skills",
        "from-my-session",
    )
    workspace = tmp_path / "repo"
    _skill(workspace / ".trae" / "skills", "zhihu")
    registry = tmp_path / ".cheese" / "skill-workspaces"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(f"{workspace}\n", encoding="utf-8")
    said = _run(tmp_path, project="proj-a", skip_homes=True)
    assert sorted(row["name"] for row in said["skills"]) == [
        "fetched",
        "from-my-session",
        "zhihu",
    ]


def test_a_listed_workspace_is_scanned_and_the_list_is_lived_in(tmp_path):
    # The registry sits under HOME; # comments, blanks and a repeat are ignored.
    workspace = tmp_path / "repo"
    _skill(workspace / ".trae" / "skills", "zhihu")
    registry = tmp_path / ".cheese" / "skill-workspaces"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        f"# my checkouts\n\n{workspace}\n{workspace}  # twice\n",
        encoding="utf-8",
    )
    said = _run(tmp_path)
    assert [row["name"] for row in said["skills"]] == ["zhihu"]
    assert said["workspaces"] == [str(workspace)]
    assert str(workspace / ".trae" / "skills") in said["roots"]


def test_a_registry_with_nothing_usable_lists_nothing(tmp_path):
    registry = tmp_path / ".cheese" / "skill-workspaces"
    registry.parent.mkdir(parents=True)
    registry.write_text("# only a comment\n\n", encoding="utf-8")
    said = _run(tmp_path)
    assert said["workspaces"] == []
    assert said["skills"] == []


def test_the_writer_program_records_what_it_is_given(tmp_path):
    program = scan.set_program()
    result = subprocess.run(
        [sys.executable, "-", "/a/one", "/a/two words"],
        input=program,
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(tmp_path)},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    written = (tmp_path / ".cheese" / "skill-workspaces").read_text(encoding="utf-8")
    assert "/a/one\n" in written
    assert "/a/two words\n" in written


def test_no_roots_at_all_is_an_empty_index_that_says_so(tmp_path):
    said = _run(tmp_path)
    assert said["skills"] == []
    assert said["roots"] == []
    assert said["problems"] == []


def test_a_failed_program_travels_as_the_machines_own_words(tmp_path):
    try:
        scan.read({"stdout": "", "stderr": "python3: not found", "exit": 127})
    except scan.ScanFailed as exc:
        assert "python3: not found" in str(exc)
    else:  # pragma: no cover - the point of the test
        raise AssertionError("a failed scan must not read as an empty one")


def test_a_short_skill_row_is_dropped_rather_than_trusted(tmp_path):
    said = scan.read(
        {
            "stdout": json.dumps({"skills": [{"name": "x"}]}),
            "exit": 0,
            "truncated": False,
        }
    )
    assert said["skills"] == []


def test_a_sessions_skills_are_read_only_for_the_project_asking(tmp_path):
    _skill(
        tmp_path / ".cheese" / "home" / "proj-a" / "gen1" / ".claude" / "skills",
        "from-my-session",
    )
    _skill(
        tmp_path / ".cheese" / "home" / "proj-b" / "gen2" / ".claude" / "skills",
        "from-another-session",
    )
    said = _run(tmp_path, project="proj-a")
    assert [row["name"] for row in said["skills"]] == ["from-my-session"]


def test_a_scan_with_no_project_does_not_read_session_homes(tmp_path):
    _skill(
        tmp_path / ".cheese" / "home" / "proj-a" / "gen1" / ".claude" / "skills",
        "from-my-session",
    )
    assert _run(tmp_path)["skills"] == []


def test_a_landed_web_skill_is_read_like_any_other(tmp_path):
    _skill(tmp_path / ".cheese" / "web-skills", "fetched")
    said = _run(tmp_path)
    assert [row["name"] for row in said["skills"]] == ["fetched"]
    assert str(tmp_path / ".cheese" / "web-skills") in said["roots"]


def test_the_land_program_writes_the_whole_skill_under_web_skills(tmp_path):
    result = _land(
        tmp_path,
        "pdf",
        {
            "SKILL.md": "---\nname: pdf\ndescription: Read PDFs.\n---\n\nBody.\n",
            "scripts/run.py": "print(1)\n",
        },
    )
    assert result.returncode == 0, result.stderr
    root = tmp_path / ".cheese" / "web-skills" / "pdf"
    assert (root / "SKILL.md").read_text(encoding="utf-8").startswith("---")
    assert (root / "scripts" / "run.py").read_text(encoding="utf-8") == "print(1)\n"
    assert json.loads(result.stdout.strip().splitlines()[-1])["path"] == str(root)


def test_the_land_program_refuses_a_path_that_climbs_out(tmp_path):
    result = _land(tmp_path, "bad", {"../escaped.md": "x"})
    assert result.returncode == 1
    assert not (tmp_path / ".cheese" / "escaped.md").exists()


def test_landing_the_same_skill_twice_leaves_it_where_it_is(tmp_path):
    files = {"SKILL.md": "---\nname: pdf\ndescription: Read PDFs.\n---\n\nBody.\n"}
    _land(tmp_path, "pdf", files)
    again = _land(tmp_path, "pdf", files)
    assert again.returncode == 0, again.stderr
    assert (tmp_path / ".cheese" / "web-skills" / "pdf" / "SKILL.md").exists()
