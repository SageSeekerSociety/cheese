"""Every exported type in frontend/src has a reader.

An ``export type`` / ``export interface`` declared in frontend/src whose name
appears exactly once in the frontend's TypeScript is declared and never used.
The scan is a word count over the source text: no node, no TypeScript parser.
Readers are the TS and Vue files under frontend/ and e2e/ (the collab server
and the browser tests import from src); Python is not counted, because a
backend ``ProjectMember`` would keep a dead TS ``ProjectMember`` alive.

A word count errs one way only: a dead type whose name is also spelled
somewhere else (another symbol, a comment) passes. It never flags a type that
has a reader.

Only types are checked. Zero-reference ``export const`` / ``function`` names
include namespace members called as ``NotificationsApi.del``, which a word
count cannot tell apart from dead code.

``_KNOWN`` freezes today's dead types by file. It may only shrink: delete a
type and drop its entry; never add one.

Deleting a type can leave another type it referenced with no reader, so
a PR that deletes types reruns this test after merging main.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FRONTEND_SRC = _REPO_ROOT / "frontend" / "src"
_READER_ROOTS = (_REPO_ROOT / "frontend", _REPO_ROOT / "e2e")
_SKIPPED_DIRS = {"node_modules", "dist"}
_SUFFIXES = {".ts", ".tsx", ".mts", ".vue"}

_DECLARATION = re.compile(
    r"^[ \t]*export\s+(?:default\s+|declare\s+)?(?:type|interface)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_WORD = re.compile(r"[A-Za-z_$][\w$]*")

_KNOWN: set[tuple[str, str]] = {
    ("composables/useReviewComments.ts", "ReviewCommentsState"),
    ("cx_types.ts", "ProjectMember"),
    ("cx_types.ts", "SpaceDashboard"),
    ("lib/docBubble.ts", "SelectionTarget"),
    ("types/apiSpaces.ts", "PublisherParticipation"),
    ("types/apiSpaces.ts", "SpaceAnalyticsTasksData"),
    ("types/canvas.ts", "CanvasTask"),
    ("types/groups.ts", "GroupMemberInvitation"),
    ("types/groups.ts", "GroupSettings"),
    ("types/groups.ts", "GroupTargetCheckIn"),
    ("views/demo/demoCatalogStudio.ts", "FrameMessage"),
}


def _sources(root: Path) -> list[Path]:
    found: list[Path] = []
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = [d for d in subdirs if d not in _SKIPPED_DIRS]
        found.extend(Path(directory, f) for f in files if Path(f).suffix in _SUFFIXES)
    return sorted(found)


def unused_exported_types(
    src: Path, readers: tuple[Path, ...] | None = None
) -> set[tuple[str, str]]:
    """``(path relative to src, name)`` of exported types named once in readers.

    ``readers`` are the roots whose files count as uses; src by default.
    """
    words: Counter[str] = Counter()
    for root in readers or (src,):
        for path in _sources(root):
            words.update(_WORD.findall(path.read_text(encoding="utf-8")))
    declared = [
        (path.relative_to(src).as_posix(), m.group(1))
        for path in _sources(src)
        for m in _DECLARATION.finditer(path.read_text(encoding="utf-8"))
    ]
    return {(rel, name) for rel, name in declared if words[name] == 1}


def test_no_new_unused_exported_types() -> None:
    new = unused_exported_types(_FRONTEND_SRC, _READER_ROOTS) - _KNOWN
    assert new == set(), (
        "exported types nobody reads; use them or delete them:\n"
        + "\n".join(f"  frontend/src/{rel}: {name}" for rel, name in sorted(new))
    )


def test_scanner_flags_unread_types_and_clears_them_once_read(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "shapes.ts").write_text(
        "export interface OrphanShape {\n  id: number\n}\n"
        "export type OrphanId = number\n"
        "export default interface OrphanDefault {\n  id: number\n}\n",
        encoding="utf-8",
    )
    assert unused_exported_types(src) == {
        ("shapes.ts", "OrphanShape"),
        ("shapes.ts", "OrphanId"),
        ("shapes.ts", "OrphanDefault"),
    }

    (src / "Reader.vue").write_text(
        '<script setup lang="ts">\n'
        "import type { OrphanShape } from './shapes'\n"
        "defineProps<{ shape: OrphanShape }>()\n"
        "</script>\n",
        encoding="utf-8",
    )
    assert unused_exported_types(src) == {
        ("shapes.ts", "OrphanId"),
        ("shapes.ts", "OrphanDefault"),
    }

    # A reader outside src counts once its root is passed, as collab/ and e2e/ are.
    (tmp_path / "e2e").mkdir()
    (tmp_path / "e2e" / "spec.ts").write_text(
        "import type { OrphanId } from '../src/shapes'\n", encoding="utf-8"
    )
    (src / "node_modules").mkdir()
    (src / "node_modules" / "dep.ts").write_text(
        "import type OrphanDefault from '../shapes'\n", encoding="utf-8"
    )
    assert unused_exported_types(src, (src, tmp_path / "e2e")) == {
        ("shapes.ts", "OrphanDefault"),
    }
