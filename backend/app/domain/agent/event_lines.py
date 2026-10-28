"""Format the platform's per-turn change summary."""

import codecs
import re
from dataclasses import dataclass

from app.core.sentences import say

# 一轮的改动汇总 (§8.4 commit 可视化): what this turn did to the topic branch, as
# ONE event at turn end. Bounded on purpose — the ask was "改了几个文件、大致
# 增删量，能点开看 diff", NOT the diff body: a 2000-line diff pasted into the
# timeline is the definition of 刷屏, and the diff panel already renders it well.
_CHANGE_FILES_LISTED = 12  # paths named in the event's text and in meta.files

# "diff --git a/<old> b/<new>", with git's quoting when a path needs it. The
# b-side is the path AFTER the change, which is where a rename should be filed.
_DIFF_HEADER_RE = re.compile(r'^diff --git "?a/.+?"? "?b/(?P<path>.+?)"?$')


@dataclass
class _Changeset:
    """What one turn did to the topic branch."""

    commits: list[str]  # newest first; commits[0] is what a UI opens
    files: list[dict]


def _diff_file_stats(diff: str) -> list[dict]:
    """Per-file added/removed line counts parsed out of a unified diff.

    Counted from the diff text rather than asked of git with ``--numstat``
    because the only workspace readers available here return diff bodies; the
    parse is the cheaper half of that trade (the body is already in memory).

    Content lines are counted only INSIDE a hunk. Skipping "+++"/"---" by prefix
    instead would silently drop a deleted line whose own text starts with "--".
    """
    stats: list[dict] = []
    current: dict | None = None
    in_hunk = False
    # Git separates patch lines with LF; other text separators are file content.
    for line in diff.split("\n"):
        header = _DIFF_HEADER_RE.match(line)
        if header is not None:
            path = header.group("path")
            if line.endswith('"'):
                # Git quotes UTF-8 bytes with C-style octal escapes.
                path = codecs.escape_decode(path.encode())[0].decode()
            current = {"path": path, "added": 0, "removed": 0}
            stats.append(current)
            in_hunk = False
            continue
        if current is None:
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk:
            continue  # index / mode / rename / ---+++ headers
        if line.startswith("+"):
            current["added"] += 1
        elif line.startswith("-"):
            current["removed"] += 1
    return stats


def _format_change_summary(files: list[dict]) -> str:
    """现场 line for a turn's changes: a headline plus the paths it touched."""
    added = sum(f["added"] for f in files)
    removed = sum(f["removed"] for f in files)
    head = say("changeSummary", count=len(files), added=added, removed=removed)
    listed = " · ".join(f["path"] for f in files[:_CHANGE_FILES_LISTED])
    rest = len(files) - min(len(files), _CHANGE_FILES_LISTED)
    if rest > 0:
        return say("changeSummaryFilesMore", head=head, files=listed, rest=rest)
    return say("changeSummaryFiles", head=head, files=listed) if listed else head


def _change_summary_meta(changeset: _Changeset) -> dict:
    """Structured payload for a turn's change summary.

    ``commit`` is the newest of the turn's commits and the ref a UI should open:
    ``GET /api/projects/{project}/git/diff?ref=<commit>`` already serves exactly
    that diff, so 「点开看 diff」 needs no new endpoint.
    """
    files = changeset.files
    return {
        "platform": True,  # the platform's own bookkeeping, not something 芝士 did
        "changeset": {
            "commit": changeset.commits[0] if changeset.commits else None,
            "commits": changeset.commits,
            "files_total": len(files),
            "added": sum(f["added"] for f in files),
            "removed": sum(f["removed"] for f in files),
            "files": files[:_CHANGE_FILES_LISTED],
            "files_omitted": max(0, len(files) - _CHANGE_FILES_LISTED),
        },
    }
