"""The machine's files as pi's tools take them from its executor
(`remote_execution/machine_files.py`). A search finds the same whether or not
the machine has ripgrep."""

import shutil
import subprocess

import pytest

from app.domain.agent.harness.claude_code.remote_execution import (
    machine_files as search,
)

SYSTEM_RG = shutil.which("rg")


def _no_rg_on_path(monkeypatch):
    which = shutil.which
    monkeypatch.setattr(
        search.shutil, "which", lambda name: None if name == "rg" else which(name)
    )


def _placed_rg(tmp_path, monkeypatch):
    """The platform's ripgrep where the toolchain fetcher puts it, standing in
    for the pinned download: the machine's own, run through a script that
    notes each time it is used."""
    toolchain = tmp_path / "toolchain"
    (toolchain / "bin").mkdir(parents=True)
    used = tmp_path / "placed-rg-used"
    rg = toolchain / "bin" / "rg"
    rg.write_text(f'#!/bin/sh\necho >> {used}\nexec {SYSTEM_RG} "$@"\n')
    rg.chmod(0o755)
    monkeypatch.setenv("CHEESE_TOOLCHAIN", str(toolchain))
    return used


@pytest.fixture(params=["ripgrep", "the platform's ripgrep", "no ripgrep"])
def machine(request, monkeypatch, tmp_path):
    monkeypatch.delenv("CHEESE_TOOLCHAIN", raising=False)
    if request.param == "no ripgrep":
        _no_rg_on_path(monkeypatch)
        return
    if SYSTEM_RG is None:
        pytest.skip("this machine has no ripgrep")
    if request.param == "the platform's ripgrep":
        _no_rg_on_path(monkeypatch)
        _placed_rg(tmp_path, monkeypatch)


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "room"
    (root / "src" / "deep").mkdir(parents=True)
    (root / "src" / "app.py").write_text("import os\nNEEDLE = 1\n")
    (root / "src" / "deep" / "util.py").write_text("needle in lower case\n")
    (root / "src" / "deep" / "notes.md").write_text("NEEDLE in notes\n")
    (root / ".github").mkdir()
    (root / ".github" / "ci.yml").write_text("NEEDLE: hidden\n")
    (root / "build").mkdir()
    (root / "build" / "out.py").write_text("NEEDLE = 'built'\n")
    (root / ".gitignore").write_text("build/\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def found(checkout, pattern, **more):
    return sorted(
        search.answer(
            {
                "operation": "glob",
                "pattern": pattern,
                "path": str(checkout),
                "limit": 100,
                **more,
            }
        )["paths"]
    )


def grepped(checkout, pattern, **more):
    answer = search.answer(
        {"operation": "grep", "pattern": pattern, "path": str(checkout), **more}
    )
    return sorted(f"{m['path']}:{m['line']}" for m in answer["matches"])


def test_find_matches_names_and_paths_and_skips_what_git_ignores(machine, checkout):
    assert found(checkout, "*.py") == ["src/app.py", "src/deep/util.py"]
    assert found(checkout, "deep/*.md") == ["src/deep/notes.md"]
    assert found(checkout, "src/**/*.{py,md}") == [
        "src/app.py",
        "src/deep/notes.md",
        "src/deep/util.py",
    ]
    # Hidden files are found, as pi's find finds them.
    assert found(checkout, "*.yml") == [".github/ci.yml"]


def test_grep_finds_lines_in_files_git_does_not_ignore(machine, checkout):
    assert grepped(checkout, "NEEDLE") == [
        ".github/ci.yml:1",
        "src/app.py:2",
        "src/deep/notes.md:1",
    ]
    assert grepped(checkout, "needle", ignoreCase=True, glob="*.py") == [
        "src/app.py:2",
        "src/deep/util.py:1",
    ]
    assert grepped(checkout, "a.p", literal=True) == []


def test_grep_stops_at_its_limit_and_shows_the_lines_around(machine, checkout):
    answer = search.answer(
        {"operation": "grep", "pattern": "NEEDLE", "path": str(checkout), "limit": 1}
    )
    assert len(answer["matches"]) == 1 and answer["limited"]

    around = search.answer(
        {
            "operation": "grep",
            "pattern": "NEEDLE",
            "path": str(checkout / "src" / "app.py"),
            "context": 1,
        }
    )
    assert around["matches"] == [
        {
            "path": "app.py",
            "line": 2,
            "lines": [[1, "import os"], [2, "NEEDLE = 1"], [3, ""]],
        }
    ]


def test_a_missing_path_is_said_to_be_missing(machine, checkout):
    answer = search.answer(
        {"operation": "grep", "pattern": "x", "path": str(checkout / "nowhere")}
    )
    assert "Path not found" in answer["error"]


def test_the_platforms_ripgrep_is_the_one_searched_with(tmp_path, monkeypatch):
    """A machine with no ripgrep of its own searches with the one the platform
    placed there, and finds what it would find with any other."""
    if SYSTEM_RG is None:
        pytest.skip("this machine has no ripgrep")
    root = tmp_path / "room"
    root.mkdir()
    (root / "app.py").write_text("NEEDLE = 1\n")
    _no_rg_on_path(monkeypatch)
    used = _placed_rg(tmp_path, monkeypatch)

    assert grepped(root, "NEEDLE") == ["app.py:1"]
    assert found(root, "*.py") == ["app.py"]
    assert len(used.read_text().splitlines()) == 2
