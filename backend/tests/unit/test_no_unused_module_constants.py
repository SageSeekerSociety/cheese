"""Guard: every ALL-CAPS module constant in backend/app is mentioned somewhere else.

A top-level ``NAME = ...`` / ``NAME: Final = ...`` that no file in the repo
mentions beyond its own definition is dead weight. The scan reads the whole
repo once into a word-frequency table (identifier-boundary tokens, so ``_N``
and ``A1`` count too) and compares each constant's count with the number of
places that define it.
"""

from __future__ import annotations

import ast
import os
import re
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_APP = _REPO / "backend" / "app"
_SKIP_DIRS = {
    "node_modules",
    ".git",
    "dist",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
_MAX_BYTES = 1_000_000
_BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".woff",
    ".woff2",
    ".ttf",
    ".lock",
    ".map",
}
_TOKEN = re.compile(r"[A-Za-z0-9_]+")
_CONST_LINE = re.compile(r"^_*[A-Z][A-Z0-9_]*\s*[:=]", re.MULTILINE)
_CONST = re.compile(r"_*[A-Z][A-Z0-9_]*$")

# "module-relative path:NAME" -> why it is still here. This test only freezes
# the debt; deleting these is a separate change (their files were in use).
_KNOWN = {
    # Public vocabulary of notice event types; kept as the one list of valid types.
    "domain/notice_meta.py:EVENT_TYPES",
    # Shared empty default for the attitudes payload, left from a refactor.
    "domain/comments/services.py:_EMPTY_ATTITUDES",
    # Page size of a GitHub search call that is now fixed by the caller.
    "domain/ci_activity/github.py:_SEARCH_PAGE",
    # Conclusions that do not count as a fine run; superseded logic moved on.
    "domain/review/superseded_runs.py:_NOT_FINE_CONCLUSIONS",
    # Event kinds meaning "still waiting"; the waiting check was inlined.
    "domain/agent/runtime.py:_WAITING",
}


def _word_counts(root: Path, exclude: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if path == exclude:
                continue
            if path.suffix in _BINARY_SUFFIXES:
                continue
            try:
                if path.stat().st_size > _MAX_BYTES:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if b"\0" in data:
                continue
            counts.update(_TOKEN.findall(data.decode("utf-8", "ignore")))
    return counts


def _defined_constants(app: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(app.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not _CONST_LINE.search(source):
            continue
        tree = ast.parse(source)
        rel = path.relative_to(app).as_posix()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for t in targets:
                if isinstance(t, ast.Name) and _CONST.match(t.id):
                    found.append((rel, t.id))
    return found


def unused_constants(repo: Path, app: Path, exclude: Path | None = None) -> set[str]:
    counts = _word_counts(repo, exclude or Path())
    defs = _defined_constants(app)
    definitions = Counter(name for _, name in defs)
    return {f"{rel}:{name}" for rel, name in defs if counts[name] <= definitions[name]}


def test_no_unused_module_constants() -> None:
    actual = unused_constants(_REPO, _APP, Path(__file__).resolve())
    assert actual - _KNOWN == set(), (
        "module constants nobody mentions (delete them, or use them): "
        f"{sorted(actual - _KNOWN)}"
    )


def test_guard_bites_on_unused_constant(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    mod = app / "mod.py"
    mod.write_text("UNUSED_THING = 1\n_N: int = 2\n", encoding="utf-8")
    assert unused_constants(tmp_path, app) == {"mod.py:UNUSED_THING", "mod.py:_N"}

    mod.write_text("UNUSED_THING = 1\n_N: int = 2\n", encoding="utf-8")
    (app / "user.py").write_text("print(UNUSED_THING + _N)\n", encoding="utf-8")
    assert unused_constants(tmp_path, app) == set()
