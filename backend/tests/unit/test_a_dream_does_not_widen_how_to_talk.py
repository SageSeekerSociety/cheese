"""Consolidation only merges and rewrites what is there: the one class of a
person's private memory that reaches multi-person places (`communication`)
can come out of a dream only from notes of that class."""

from app.domain.memory.dream import talk_widened


def _note(name: str, kind: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {body}\ntype: {kind}\n---\n\n{body}\n"


SHORT = _note("short", "communication", "回复简短")
NO_AT = _note("no-at", "communication", "不必 @ 她")
REVIEW = _note("review", "feedback", "交给她的改动多核对一遍")


def test_two_notes_of_the_class_merged_into_one_is_a_consolidation():
    before = {"short.md": SHORT, "no-at.md": NO_AT}
    after = {"talk.md": _note("talk", "communication", "回复简短，不必 @ 她")}
    assert not talk_widened(before, after)


def test_a_note_of_another_type_turned_into_the_class_is_refused():
    before = {"review.md": REVIEW}
    after = {"review.md": _note("review", "communication", "交给她的改动多核对一遍")}
    assert talk_widened(before, after)


def test_a_new_note_of_the_class_out_of_nothing_of_it_is_refused():
    before = {"review.md": REVIEW, "short.md": SHORT}
    after = {**before, "extra.md": _note("extra", "communication", "多核对")}
    assert talk_widened(before, after)
