"""Three-way merge of a text file, line by line.

A person opens a file, 芝士 changes it meanwhile, and the person saves: the
version they opened (`base`), what they want to save (`mine`) and what is on
disk now (`theirs`). Each side's changes are read as edits of `base`; edits that
touch different lines both apply, the same edit on both sides applies once, and
edits that touch the same or adjacent lines differently are a conflict the
person picks. Adjacent counts as touching, as it does for git: two edits with
no line between them usually belong together, and guessing their order is how a
merge silently produces code neither side wrote.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class Region:
    """A stretch of the merged file: text both sides agree on, or a conflict."""

    text: str = ""
    conflict: bool = False
    base: str = ""
    mine: str = ""
    theirs: str = ""

    def as_dict(self) -> dict:
        if not self.conflict:
            return {"kind": "same", "text": self.text}
        return {
            "kind": "conflict",
            "base": self.base,
            "mine": self.mine,
            "theirs": self.theirs,
        }


def _edits(base: list[str], other: list[str]) -> list[tuple[int, int, int, int]]:
    matcher = SequenceMatcher(None, base, other, autojunk=False)
    return [
        (i1, i2, j1, j2)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]


def _apply(
    base: list[str],
    other: list[str],
    edits: list[tuple[int, int, int, int]],
    start: int,
    end: int,
) -> list[str]:
    out: list[str] = []
    at = start
    for i1, i2, j1, j2 in edits:
        out += base[at:i1]
        out += other[j1:j2]
        at = i2
    out += base[at:end]
    return out


def merge(base_text: str, mine_text: str, theirs_text: str) -> list[Region]:
    """The merged file as regions; it merged cleanly when none is a conflict."""
    base = base_text.splitlines(keepends=True)
    mine = mine_text.splitlines(keepends=True)
    theirs = theirs_text.splitlines(keepends=True)
    tagged = sorted(
        [(e, "mine") for e in _edits(base, mine)]
        + [(e, "theirs") for e in _edits(base, theirs)],
        key=lambda item: (item[0][0], item[0][1]),
    )
    # Group the edits whose base ranges overlap or touch.
    groups: list[tuple[int, int, list]] = []
    for edit, side in tagged:
        start, end = edit[0], edit[1]
        if groups and start <= groups[-1][1]:
            g_start, g_end, members = groups[-1]
            groups[-1] = (g_start, max(g_end, end), [*members, (edit, side)])
        else:
            groups.append((start, end, [(edit, side)]))

    regions: list[Region] = []
    same: list[str] = []
    at = 0
    for start, end, members in groups:
        same += base[at:start]
        sides = {side for _edit, side in members}
        mine_part = _apply(
            base, mine, [e for e, s in members if s == "mine"], start, end
        )
        theirs_part = _apply(
            base, theirs, [e for e, s in members if s == "theirs"], start, end
        )
        if sides == {"mine"}:
            same += mine_part
        elif sides == {"theirs"} or mine_part == theirs_part:
            same += theirs_part
        else:
            if same:
                regions.append(Region(text="".join(same)))
                same = []
            regions.append(
                Region(
                    conflict=True,
                    base="".join(base[start:end]),
                    mine="".join(mine_part),
                    theirs="".join(theirs_part),
                )
            )
        at = end
    same += base[at:]
    if same:
        regions.append(Region(text="".join(same)))
    return regions


def merged_text(regions: list[Region]) -> str | None:
    """The file, when no region is a conflict."""
    if any(region.conflict for region in regions):
        return None
    return "".join(region.text for region in regions)
