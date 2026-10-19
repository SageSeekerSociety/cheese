"""The answer list and the question list show the same author card.

The card is a contract (`docs/design-system.md` §3.14). A field added for one
list must not leave the other behind, and both lists must agree on how a person
who never picked an avatar looks. These tests judge that from outside: the two
read paths hand back the same card for the same profile.
"""

from types import SimpleNamespace

from app.domain.answers.services import profile_to_author as answer_author
from app.domain.questions.services import profile_to_author as question_author


def _profile(**overrides):
    values = {"user_id": 3, "nickname": "bob", "avatar_id": 7, "intro": "hey"}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_the_two_read_paths_use_one_author_card():
    assert answer_author is question_author


def test_both_lists_show_the_same_card_for_the_same_person():
    profile = _profile()
    assert answer_author(profile, chosen_avatar_id=9) == question_author(
        profile, chosen_avatar_id=9
    )


def test_the_card_carries_the_contract_fields_and_nothing_else():
    card = answer_author(_profile(), chosen_avatar_id=9)
    assert card == {
        "id": 3,
        "nickname": "bob",
        "avatarId": 9,
        "intro": "hey",
    }
    assert set(card) == {"id", "nickname", "avatarId", "intro"}


def test_a_person_who_never_picked_an_avatar_has_none_on_either_list():
    profile = _profile(avatar_id=7)
    assert answer_author(profile)["avatarId"] is None
    assert question_author(profile)["avatarId"] is None


def test_no_profile_means_no_author_on_either_list():
    assert answer_author(None) is None
    assert question_author(None) is None
