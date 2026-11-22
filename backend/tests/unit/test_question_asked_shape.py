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


def test_a_typed_reply_written_before_2026_11_22_still_counts_as_the_answer():
    """老版本把打字回的那句写成 ``pending``，等提问的芝士确认。现在打字当场就算作答，
    老数据也按作答读——否则那道题会一直显示「还没答」，而且没人能把它结掉。"""
    from app.domain.block.asked import answered, final_answer

    meta = {
        "asked": ["mila"],
        "answer_log": [
            {
                "kind": "pending",
                "by": "mila",
                "note": "这是什么意思？",
                "at": "2026-11-17T10:00:00Z",
            }
        ],
    }
    assert answered(meta)
    assert settled(meta)
    assert final_answer(meta)["note"] == "这是什么意思？"


def test_saying_it_again_keeps_both_and_the_last_one_counts():
    from app.domain.block.asked import earlier_answers, final_answer

    meta = {
        "asked": ["mila"],
        "answer_log": [
            {"kind": "note", "by": "mila", "note": "你先帮老师发个十几题"},
            {"kind": "option", "by": "mila", "option": "我自己来"},
        ],
    }
    assert final_answer(meta)["option"] == "我自己来"
    assert [e["note"] for e in earlier_answers(meta)] == ["你先帮老师发个十几题"]


def test_only_the_person_who_answered_may_say_it_again():
    from app.domain.block.asked import answerable, answerable_by

    meta = {
        "asked": ["mila", "bob"],
        "answer_log": [{"kind": "note", "by": "mila", "note": "按季度"}],
    }
    assert not answerable(meta)
    assert answerable_by(meta, "mila")
    assert not answerable_by(meta, "bob")


def test_a_confirmed_reply_answers_it():
    from app.domain.block.asked import answered

    meta = {"asked": ["mila"], "answer_log": [{"kind": "note", "by": "mila"}]}
    assert answered(meta)
    assert settled(meta)
