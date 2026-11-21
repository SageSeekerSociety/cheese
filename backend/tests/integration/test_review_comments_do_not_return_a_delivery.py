"""A PR comment is delivered, not treated as a reviewer's rejection.

The quota notice is the actual COMMENTED review on PR #5352; GitHub's
CHANGES_REQUESTED state, CI failures and other stops remain authoritative.
"""

import pytest

from app.domain.review.notes import NoteCode
from tests.integration.test_nudge_pr_signals import (
    _authorized,
    _card_row,
    _cards,
    _comment,
    _nudges,
    _poll,
    _review,
    _set_note,
)
from tests.integration.test_nudge_pr_signals import app_world as app_world

QUOTA_NOTICE = (
    "Copilot was unable to review this pull request because the user who "
    "requested the review has insufficient remaining quota. Try requesting "
    "a review at a lower effort level."
)


@pytest.mark.parametrize("inline", [False, True])
def test_an_ordinary_comment_is_delivered_without_returning_the_card(
    client, app_world, inline
):
    fake = app_world["fake"]
    room, _cid, number, _head = _authorized(client, app_world)
    fake.reviews_by_number[number] = [
        _comment(1, body="This branch needs a null check")
        if inline
        else _review(1, kind="commented", body=QUOTA_NOTICE)
    ]

    _poll(client)

    card = _cards(client, room)[0]
    assert card["status"] == "pending"
    assert card["note_level"] != "error"
    assert len(_nudges(client, room, "pr_review")) == 1
    assert ("null check" if inline else "insufficient remaining quota") in _nudges(
        client, room, "pr_review"
    )[0]["meta"]["detail"]
    _poll(client)
    assert len(_nudges(client, room, "pr_review")) == 1


def test_rechecking_the_same_plain_comment_repairs_only_its_own_old_stop(
    client, app_world
):
    fake = app_world["fake"]
    room, cid, number, head = _authorized(client, app_world)
    fake.reviews_by_number[number] = [_review(1, kind="commented", body=QUOTA_NOTICE)]
    _poll(client)
    # Preserve the dispatched event's signature and exact generated note: an
    # old release marked this notice accept_pr_stalled instead of informational.
    old_text = _card_row(client, cid)["note"]
    _set_note(client, cid, code=NoteCode.accept_pr_stalled, text=old_text)

    _poll(client)

    card = _cards(client, room)[0]
    assert card["note"] == old_text
    assert card["note_level"] == "info"
    assert card["merge_state"]["head_sha"] == head
    assert len(_nudges(client, room, "pr_review")) == 1


@pytest.mark.parametrize("other_stop", [False, True])
def test_real_change_requests_and_unrelated_same_code_stops_are_not_cleared(
    client, app_world, other_stop
):
    fake = app_world["fake"]
    room, cid, number, _head = _authorized(client, app_world)
    fake.reviews_by_number[number] = [
        _review(1, kind="commented", body=QUOTA_NOTICE)
        if other_stop
        else _review(1, body="The data loss must be fixed before merging")
    ]
    _poll(client)
    if other_stop:
        _set_note(
            client,
            cid,
            code=NoteCode.accept_pr_stalled,
            text="PR closed by its owner; needs a decision",
        )
        # A fresh ordinary comment must not erase a different, still-live stop.
        fake.reviews_by_number[number].append(
            _comment(2, body="Consider a clearer name")
        )
    _poll(client)

    assert _cards(client, room)[0]["note_level"] == "error"


def test_a_new_failed_head_is_not_hidden_by_rechecking_an_old_comment(
    client, app_world
):
    fake = app_world["fake"]
    room, cid, number, head = _authorized(client, app_world)
    fake.reviews_by_number[number] = [_review(1, kind="commented", body=QUOTA_NOTICE)]
    _poll(client)
    _set_note(
        client,
        cid,
        code=NoteCode.accept_pr_stalled,
        text=_card_row(client, cid)["note"],
    )
    new_head = f"{head}-new"
    fake.prs[number]["head_sha"] = new_head
    fake.check_state_by_sha[new_head] = ("failure", "Backend: regression")

    _poll(client)

    assert _card_row(client, cid)["note_code"] is NoteCode.checks_failed
    assert "regression" in _cards(client, room)[0]["note"]


def _signal(
    rid: int, *, kind: str = "changes_requested", body: str, superseded: bool = False
):
    from app.domain.review.pr_signals import ReviewSignal

    return ReviewSignal(
        id=f"review:{rid}",
        kind=kind,
        author="bob",
        body=body,
        superseded=superseded,
    )


def test_a_withdrawn_change_request_is_delivered_but_stops_blocking(client, app_world):
    """The reviewer approved on top of their earlier request. The words still
    reach the teammate; the card is not pressed red by a request that is gone."""
    fake = app_world["fake"]
    room, cid, number, _head = _authorized(client, app_world)
    fake.reviews_by_number[number] = [
        _signal(1, body="先补空值检查", superseded=True),
        _signal(2, kind="commented", body="现在可以了"),
    ]

    _poll(client)

    card = _cards(client, room)[0]
    assert card["status"] == "pending"
    assert card["note_level"] != "error"
    assert "先补空值检查" in _nudges(client, room, "pr_review")[0]["meta"]["detail"]


def test_an_unwithdrawn_change_request_still_blocks(client, app_world):
    fake = app_world["fake"]
    room, cid, number, _head = _authorized(client, app_world)
    fake.reviews_by_number[number] = [_signal(2, body="还是得改")]

    _poll(client)

    assert _cards(client, room)[0]["note_level"] == "error"


def test_a_mixed_batch_keeps_the_live_request_blocking(client, app_world):
    """One reviewer withdrew their request by approving; another still wants
    changes. The card must stay blocked — the old stop is not cleared by the
    presence of a withdrawn one."""
    fake = app_world["fake"]
    room, cid, number, _head = _authorized(client, app_world)
    withdrawn = _signal(1, body="先补空值检查", superseded=True)
    live = _signal(3, body="还要加测试")
    fake.reviews_by_number[number] = [withdrawn, live]

    _poll(client)

    card = _cards(client, room)[0]
    assert card["note_level"] == "error"
    assert "还要加测试" in _nudges(client, room, "pr_review")[0]["meta"]["detail"]
