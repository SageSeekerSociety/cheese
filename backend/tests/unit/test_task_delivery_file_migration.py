"""Legacy room artifacts stay readable and their originals remain untouched."""

import importlib.util
import subprocess
import uuid
from pathlib import Path

import pytest


def migration():
    path = (
        Path(__file__).parents[2]
        / "alembic/versions/d7a419be028c_tasks_own_delivery.py"
    )
    spec = importlib.util.spec_from_file_location("room_file_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_room_files_follow_the_old_pointer_and_preserve_originals(tmp_path):
    project, room, tree = [str(uuid.uuid4()) for _ in range(3)]
    pointer = tmp_path / ".place-trees" / room.replace("-", "")
    pointer.parent.mkdir()
    pointer.write_text(tree)
    source = tmp_path / ".worktrees" / project / f"topic_{tree.replace('-', '')[:8]}"
    source.mkdir(parents=True)
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    (source / "report.html").write_text("<h1>Published</h1>")
    (source / "unfinished.txt").write_text("not committed")
    (source / ".gitignore").write_text("private.key\n")
    (source / "private.key").write_text("fixture secret")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside the room")
    (source / "escape").symlink_to(outside)
    subprocess.run(
        ["git", "-C", str(source), "add", "report.html", ".gitignore"],
        check=True,
        capture_output=True,
    )
    target = tmp_path / ".room-files" / project / room
    staging = target.with_name(f".{room}.importing")
    staging.mkdir(parents=True)
    (staging / "report.html").write_text("interrupted copy")
    move = migration().preserve_room_files
    move(tmp_path, project, room)
    assert (target / "report.html").read_text() == "<h1>Published</h1>"
    assert (target / "unfinished.txt").read_text() == "not committed"
    assert not (target / "private.key").exists()
    assert not (target / "escape").exists()
    assert (source / "report.html").read_text() == "<h1>Published</h1>"
    assert (source / "unfinished.txt").read_text() == "not committed"
    (target / "report.html").write_text("new room publication")
    move(tmp_path, project, room)
    assert (target / "report.html").read_text() == "new room publication"


def test_migration_refuses_to_overwrite_existing_room_files(tmp_path):
    target = tmp_path / ".room-files" / "project" / "room"
    target.mkdir(parents=True)
    (target / "report.html").write_text("existing publication")
    with pytest.raises(RuntimeError, match="already exists"):
        migration().preserve_room_files(tmp_path, "project", "room")
    assert (target / "report.html").read_text() == "existing publication"
