"""Where a comment's lines are in a later version of the file.

A comment is written on lines of one version; 芝士 then changes the file and
hands it over again, and the same lines now sit elsewhere, or are gone. The
comment remembers the lines' text, and this finds that text again: the match
nearest the old position wins, so a block that appears twice lands on the copy
that moved least. Trailing whitespace does not count, since editors change it.
"""


def _norm(line: str) -> str:
    return line.rstrip()


def find(content: str, line_text: str, near: int) -> int | None:
    """The 1-based line where `line_text` starts in `content`; None when gone."""
    wanted = [_norm(line) for line in line_text.splitlines()]
    while wanted and not wanted[-1]:
        wanted.pop()
    if not wanted or not any(wanted):
        return None
    lines = [_norm(line) for line in content.splitlines()]
    span = len(wanted)
    starts = [
        i + 1 for i in range(len(lines) - span + 1) if lines[i : i + span] == wanted
    ]
    if not starts:
        return None
    return min(starts, key=lambda start: (abs(start - near), start))
