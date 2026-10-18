"""Every exported type in frontend/src has a reader.

An ``export type`` / ``export interface`` whose name appears exactly once in
frontend/src is declared and never used. The scan is a word count over the
source text: no node, no TypeScript parser. The universe is frontend/src
only, because a TS type can only be read by TS; counting backend files would
let a Python ``ProjectMember`` keep a dead TS ``ProjectMember`` alive.

Only types are checked. Zero-reference ``export const`` / ``function`` names
include namespace members called as ``NotificationsApi.del``, which a word
count cannot tell apart from dead code.

``_KNOWN`` freezes today's dead types by file. It may only shrink: delete a
type and drop its entry; never add one.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FRONTEND_SRC = _REPO_ROOT / "frontend" / "src"
_SUFFIXES = {".ts", ".tsx", ".mts", ".vue"}

_DECLARATION = re.compile(
    r"^[ \t]*export[ \t]+(?:declare[ \t]+)?(?:type|interface)[ \t]+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_WORD = re.compile(r"[A-Za-z_$][\w$]*")

_KNOWN: set[tuple[str, str]] = {
    ("composables/useReviewComments.ts", "ReviewCommentsState"),
    ("cx_types.ts", "ProjectMember"),
    ("cx_types.ts", "SpaceDashboard"),
    ("lib/docBubble.ts", "SelectionTarget"),
    ("types/apiNotifications.ts", "BulkUpdateRequest"),
    ("types/apiNotifications.ts", "BulkUpdateResponse"),
    ("types/apiSpaces.ts", "PublisherParticipation"),
    ("types/apiSpaces.ts", "SpaceAnalyticsTasksData"),
    ("types/apiUsers.ts", "OAuthBindUserResponse"),
    ("types/apiUsers.ts", "OAuthCreateUserResponse"),
    ("types/apiUsers.ts", "OAuthVerifyRequest"),
    ("types/apiUsers.ts", "PasskeyAuthenticationOptionsResponse"),
    ("types/apiUsers.ts", "PasskeyRegistrationOptionsResponse"),
    ("types/canvas.ts", "CanvasTask"),
    ("types/groups.ts", "GroupMemberInvitation"),
    ("types/groups.ts", "GroupSettings"),
    ("types/groups.ts", "GroupTargetCheckIn"),
    ("types/insights.ts", "InsightMediaImage"),
    ("types/insights.ts", "InsightMediaVideo"),
    ("types/ownAgents.ts", "MyOwnAgentConnection"),
    ("types/ownAgents.ts", "OwnAgentRow"),
    ("views/demo/demoCatalogStudio.ts", "FrameMessage"),
}


def unused_exported_types(root: Path) -> set[tuple[str, str]]:
    """``(path relative to root, name)`` of exported types named once under root."""
    words: Counter[str] = Counter()
    declared: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in _SUFFIXES or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        words.update(_WORD.findall(text))
        relative = path.relative_to(root).as_posix()
        declared.extend((relative, m.group(1)) for m in _DECLARATION.finditer(text))
    return {(rel, name) for rel, name in declared if words[name] == 1}


def test_no_new_unused_exported_types() -> None:
    new = unused_exported_types(_FRONTEND_SRC) - _KNOWN
    assert new == set(), (
        "exported types nobody reads; use them or delete them:\n"
        + "\n".join(f"  frontend/src/{rel}: {name}" for rel, name in sorted(new))
    )


def test_scanner_flags_an_unread_interface_and_clears_once_imported(
    tmp_path: Path,
) -> None:
    (tmp_path / "shapes.ts").write_text(
        "export interface OrphanShape {\n  id: number\n}\n", encoding="utf-8"
    )
    assert unused_exported_types(tmp_path) == {("shapes.ts", "OrphanShape")}

    (tmp_path / "Reader.vue").write_text(
        '<script setup lang="ts">\n'
        "import type { OrphanShape } from './shapes'\n"
        "defineProps<{ shape: OrphanShape }>()\n"
        "</script>\n",
        encoding="utf-8",
    )
    assert unused_exported_types(tmp_path) == set()
