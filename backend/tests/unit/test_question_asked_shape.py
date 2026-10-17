"""A question written by a backend from before the addressee list still waits on
its one person: during a blue/green deploy the old backend keeps writing
``meta.asked`` as a bare handle after the migration has run."""

from app.domain.block.asked import asked_of, settled, skipped_by_all


def test_a_bare_handle_names_one_person():
    assert asked_of({"asked": "mila"}) == ("mila",)


def test_a_bare_handle_question_is_settled_when_that_person_skips():
    meta = {"asked": "mila", "answer_log": [{"kind": "skipped", "by": "mila"}]}
    assert skipped_by_all(meta)
    assert settled(meta)
