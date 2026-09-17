"""What changed in a living doc, in one line.

Used to tell a RUNNING session that a person edited the doc under it. Not the
document — 芝士 re-reads that when it needs it, and a full doc pushed into the
middle of a turn is a wall of text that displaces the work being done. What the
turn actually needs is: something moved, roughly here, and the version you are
holding is no longer the current one.
"""

import difflib
import re

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
# A heading is a title, and one that runs past this is prose the writer put in
# the wrong place. Trimmed rather than dropped: the first words still locate it.
_HEADING_CHARS = 24
# Past a few, naming sections stops locating anything — "改了这五处" is the same
# information as "改了很多处", at five times the length.
_MAX_SECTIONS = 3


def _sections(lines: list[str]) -> list[str]:
    """Which heading each line sits under ("" before the first one)."""
    out: list[str] = []
    current = ""
    for line in lines:
        match = _HEADING.match(line)
        if match:
            current = match.group(2).strip()
        out.append(current)
    return out


def _trim(heading: str) -> str:
    return (
        heading
        if len(heading) <= _HEADING_CHARS
        else heading[:_HEADING_CHARS].rstrip() + "…"
    )


def summarize_doc_change(before: str, after: str) -> str:
    """One line naming what moved between two versions of a living doc.

    Structural, not semantic: which sections were touched and how much. A
    semantic summary would need a model call, and this runs on the human's save
    — the thing being answered is 「要不要重读」, which the structure answers.
    """
    if not before.strip():
        return "新建了这篇文档"
    if not after.strip():
        return "清空了这篇文档"

    old_lines = before.splitlines()
    new_lines = after.splitlines()
    old_sections = _sections(old_lines)
    new_sections = _sections(new_lines)

    added = removed = 0
    touched: list[str] = []
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed += i2 - i1
        added += j2 - j1
        # Both sides: a section can be touched by having lines taken out of it
        # as much as by having lines put in, and a section deleted outright
        # appears only on the old side.
        for section in old_sections[i1:i2] + new_sections[j1:j2]:
            if section and section not in touched:
                touched.append(section)

    if not (added or removed):
        return "重存了一次，内容没变"

    size = f"+{added} −{removed} 行"
    if not touched:
        return f"改动 {size}"
    named = "、".join(f"「{_trim(s)}」" for s in touched[:_MAX_SECTIONS])
    more = " 等几处" if len(touched) > _MAX_SECTIONS else ""
    return f"改动涉及{named}{more}，{size}"
