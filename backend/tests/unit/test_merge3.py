"""Three-way merge of a person's save with what changed the file meanwhile.

Rules: edits on different lines both apply; the same edit made on both sides
applies once; edits on the same or adjacent lines are a conflict that keeps
both versions for the person to pick, and no merged file is produced.
"""

from app.domain.repository.merge3 import merge, merged_text

BASE = "a\nb\nc\nd\ne\nf\n"


def test_edits_on_different_lines_both_apply():
    mine = "A\nb\nc\nd\ne\nf\n"
    theirs = "a\nb\nc\nd\ne\nF\n"
    assert merged_text(merge(BASE, mine, theirs)) == "A\nb\nc\nd\ne\nF\n"


def test_the_same_edit_on_both_sides_applies_once():
    both = "a\nb\nC\nd\ne\nf\n"
    assert merged_text(merge(BASE, both, both)) == both


def test_one_side_unchanged_takes_the_other():
    theirs = "a\nb\nc\nd\nx\ny\ne\nf\n"
    assert merged_text(merge(BASE, BASE, theirs)) == theirs


def test_different_edits_of_the_same_line_conflict_and_keep_both():
    mine = "a\nb\nmine\nd\ne\nf\n"
    theirs = "a\nb\ntheirs\nd\ne\nf\n"
    regions = merge(BASE, mine, theirs)
    assert merged_text(regions) is None
    (conflict,) = [r for r in regions if r.conflict]
    assert (conflict.base, conflict.mine, conflict.theirs) == ("c\n", "mine\n", "theirs\n")
    # Everything around the conflict is still there, in order.
    text = "".join(r.text if not r.conflict else "|" for r in regions)
    assert text == "a\nb\n|d\ne\nf\n"


def test_edits_on_adjacent_lines_conflict():
    mine = "a\nB\nc\nd\ne\nf\n"
    theirs = "a\nb\nC\nd\ne\nf\n"
    assert merged_text(merge(BASE, mine, theirs)) is None
