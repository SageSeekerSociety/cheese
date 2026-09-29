"""Skills a room offers only once a file tool reaches their files.

Plain Claude Code offers a subdirectory's `.claude/skills` from the first time
a file under that subdirectory is read or written, and a skill whose `paths`
scopes it to some files from the first time one of them is. A room's file
tools run on the executor, so the room links these into the session's config
dir itself when one returns (`release.touch_skills`). The whole scenario, held
against plain Claude Code, is `scripts/remote_execution/skills.py`.
"""

import json
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code.remote_execution import release

WORKSPACE = "/work/the project"


def _room(tmp_path, files):
    """A session dir, its config dir, and the view of a project holding
    `files` ({path: (text, paths or None)}), with its context tree saved."""
    session, config, view = tmp_path / "session", tmp_path / "config", tmp_path / "view"
    session.mkdir()
    (config / "skills").mkdir(parents=True)
    entries = {}
    for name, (text, paths) in files.items():
        (view / name).parent.mkdir(parents=True, exist_ok=True)
        (view / name).write_text(text)
        parts = Path(name).parts
        for depth in range(1, len(parts)):
            entries["/".join(parts[:depth])] = {"kind": "directory"}
        entries[name] = {"kind": "file", **({"paths": paths} if paths else {})}
    tree = {"generation": "one", "entries": entries}
    (session / "context-tree.json").write_text(json.dumps(tree))
    release.link_forwarded_user_context(
        session, config, WORKSPACE, tree, tmp_path / "helpers", view=view
    )
    return session, config, view


def _touch(tmp_path, room, path):
    session, config, view = room
    return release.touch_skills(
        session, config, WORKSPACE, tmp_path / "helpers", f"{WORKSPACE}/{path}", view
    )


def _places(session):
    return json.loads((session / release.SKILL_PLACES).read_text())["places"]


def test_a_subdirectorys_skill_is_offered_once_a_file_there_is_reached(tmp_path):
    room = _room(
        tmp_path,
        {
            "pkg/.claude/skills/tool/SKILL.md": ("TOOL\n", None),
            "pkg/.claude/skills/tool/reference.md": ("REF\n", None),
        },
    )
    session, config, _ = room
    assert not (config / "skills/tool").exists()

    assert _touch(tmp_path, room, "elsewhere/file.txt") is False
    assert not (config / "skills/tool").exists()

    assert _touch(tmp_path, room, "pkg/deep/file.txt") is True
    assert (config / "skills/tool").resolve() == Path(
        f"{WORKSPACE}/pkg/.claude/skills/tool"
    )
    assert _places(session)["tool"] == {
        "place": f"{WORKSPACE}/pkg/.claude/skills/tool",
        "note": " (from pkg/.claude/skills — applies when working on files under pkg/)",
    }
    # The runner's next catch-up reloads the session's skills.
    assert (session / release.SKILLS_CHANGED).exists()
    # Reached again, nothing changes.
    assert _touch(tmp_path, room, "pkg/file.txt") is False


def test_a_name_the_root_uses_is_qualified_with_the_directory(tmp_path):
    room = _room(
        tmp_path,
        {
            ".claude/skills/deploy/SKILL.md": ("ROOT\n", None),
            "apps/web/.claude/skills/deploy/SKILL.md": ("WEB\n", None),
        },
    )
    session, config, _ = room

    _touch(tmp_path, room, "apps/web/index.html")

    assert (config / "skills/deploy").resolve() == Path(
        f"{WORKSPACE}/.claude/skills/deploy"
    )
    entry = "apps∕web:deploy"
    assert (config / "skills" / entry).resolve() == Path(
        f"{WORKSPACE}/apps/web/.claude/skills/deploy"
    )
    assert _places(session)[entry]["note"].startswith(
        ' (scoped to apps/web/ — use this instead of the unscoped "deploy" skill'
    )


def test_a_path_scoped_skill_reached_is_offered_without_its_paths(tmp_path):
    text = "---\nname: py\npaths:\n  - src/**\ndescription: Python.\n---\nBODY\n"
    room = _room(tmp_path, {".claude/skills/py/SKILL.md": (text, ["src/**"])})
    session, config, _ = room
    # Linked as it is, the session itself holds it back until a Read.
    assert (config / "skills/py").is_symlink()

    assert _touch(tmp_path, room, "docs/readme.md") is False
    assert _touch(tmp_path, room, "src/new/module.py") is True

    written = config / "skills/py/SKILL.md"
    assert not (config / "skills/py").is_symlink()
    assert written.read_text() == "---\nname: py\ndescription: Python.\n---\nBODY\n"
    assert _places(session)["py"]["place"] == f"{WORKSPACE}/.claude/skills/py"

    # A new session process has reached nothing, and the copy is not taken for
    # a skill the platform shipped.
    release.forget_touched_skills(session, config)
    assert not (config / "skills/py").exists()


@pytest.mark.parametrize(
    ("paths", "file", "offered"),
    [
        (["src/**"], "src/a.py", True),
        (["src/**"], "lib/src/a.py", True),
        (["src/**"], "srcs/a.py", False),
        (["/src/*.py"], "src/a.py", True),
        (["/src/*.py"], "lib/src/a.py", False),
        (["**/*.test.ts"], "a/b/c.test.ts", True),
        (["*.{md,txt}"], "notes/x.txt", True),
        (["docs/, *.rst"], "docs/x.md", True),
        (["docs/, *.rst"], "x.rst", True),
        (["out/**/*.txt"], "out/deep/new.txt", True),
        (["out/**/*.txt"], "out/new.md", False),
        (["**"], "anything", None),
    ],
)
def test_paths_are_read_as_claude_code_reads_them(paths, file, offered):
    patterns = release.path_patterns(paths)
    if offered is None:
        assert patterns is None
    else:
        assert release.path_matches(patterns, file) is offered
