"""A `CHANGES_REQUESTED` a reviewer has since approved or dismissed stops blocking.

GitHub's review list is append-only: a reviewer who asks for changes and later
approves leaves BOTH rows in `GET /pulls/{n}/reviews`, the old one still
reading `CHANGES_REQUESTED`. Treating it as a live stop is the same disease as
the one PR #5359 fixed for ordinary comments — a thing that was true once
pinning the card red forever.

Only a reviewer's LAST verdict counts. A later plain comment is not a verdict
(reviewing again says nothing about the change request), and one reviewer's
approval never cancels another reviewer's request.
"""

from app.domain.review.pr_signals import (
    ReviewSignal,
    superseded_change_requests,
)


def review(rid: int, login: str, state: str, at: str) -> dict:
    return {
        "id": rid,
        "user": {"login": login} if login else {},
        "state": state,
        "submitted_at": at,
    }


def test_an_approval_after_a_change_request_supersedes_it():
    assert superseded_change_requests(
        [
            review(1, "bob", "CHANGES_REQUESTED", "2026-01-01T00:00:00Z"),
            review(2, "bob", "APPROVED", "2026-01-02T00:00:00Z"),
        ]
    ) == {"1"}


def test_a_dismissal_after_a_change_request_supersedes_it():
    assert superseded_change_requests(
        [
            review(1, "bob", "CHANGES_REQUESTED", "2026-01-01T00:00:00Z"),
            review(2, "bob", "DISMISSED", "2026-01-02T00:00:00Z"),
        ]
    ) == {"1"}


def test_a_lone_change_request_still_stops():
    assert (
        superseded_change_requests(
            [review(1, "bob", "CHANGES_REQUESTED", "2026-01-01T00:00:00Z")]
        )
        == set()
    )


def test_a_change_request_after_an_approval_still_stops():
    """The order matters: the reviewer looked again and withdrew the approval."""
    assert (
        superseded_change_requests(
            [
                review(1, "bob", "APPROVED", "2026-01-01T00:00:00Z"),
                review(2, "bob", "CHANGES_REQUESTED", "2026-01-02T00:00:00Z"),
            ]
        )
        == set()
    )


def test_a_later_plain_comment_is_not_a_verdict():
    assert (
        superseded_change_requests(
            [
                review(1, "bob", "CHANGES_REQUESTED", "2026-01-01T00:00:00Z"),
                review(2, "bob", "COMMENTED", "2026-01-02T00:00:00Z"),
            ]
        )
        == set()
    )


def test_one_reviewers_approval_does_not_cancel_anothers_request():
    assert (
        superseded_change_requests(
            [
                review(1, "bob", "CHANGES_REQUESTED", "2026-01-01T00:00:00Z"),
                review(2, "carol", "APPROVED", "2026-01-02T00:00:00Z"),
            ]
        )
        == set()
    )


def test_an_unknown_time_supersedes_nothing():
    """Guessing wrong here would clear a real request; guessing "still live"
    only leaves the card red a while longer."""
    assert (
        superseded_change_requests(
            [
                review(1, "bob", "CHANGES_REQUESTED", ""),
                review(2, "bob", "APPROVED", ""),
            ]
        )
        == set()
    )


def test_forgejo_state_words_are_mapped():
    assert superseded_change_requests(
        [
            {
                "id": 1,
                "user": {"login": "bob"},
                "state": "REQUEST_CHANGES",
                "submitted_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": 2,
                "user": {"login": "bob"},
                "state": "APPROVE",
                "submitted_at": "2026-01-02T00:00:00Z",
            },
        ],
        states={"REQUEST_CHANGES": "CHANGES_REQUESTED", "APPROVE": "APPROVED"},
    ) == {"1"}


def test_the_signal_says_it_was_superseded_but_keeps_its_text():
    """It still reaches the teammate — the words are kept; only the stop goes."""
    signal = ReviewSignal(
        id="review:1",
        kind="changes_requested",
        author="bob",
        body="请加空值检查",
        superseded=True,
    )
    assert "请加空值检查" in signal.line()
    assert "已被这位评审人此后的评审取代" in signal.line()
    live = ReviewSignal(
        id="review:2", kind="changes_requested", author="bob", body="再加一行测试"
    )
    assert "已被" not in live.line()


def test_the_nudge_head_depends_on_an_unwithdrawn_request():
    """`_review_nudge` turns the withdrawn request into a notice, not a stop —
    and remembers the line it WOULD have written, so a card that was pressed
    red by the same batch can be reclassified in place."""
    from app.core.sentences import say

    class _Card:
        pr_number = 7
        pr_url = "https://example/pull/7"
        nudge_state = None

    class _Creds:
        read = "t"

    class _Client:
        async def review_signals(self, **_kwargs):
            return [
                ReviewSignal(
                    id="review:1",
                    kind="changes_requested",
                    author="bob",
                    body="先补测试",
                    superseded=True,
                )
            ]

    import asyncio

    from app.domain.review.nudges import _review_nudge

    nudge = asyncio.run(
        _review_nudge(
            card=_Card(),  # type: ignore[arg-type]
            owner="acme",
            repo="widgets",
            creds=_Creds(),  # type: ignore[arg-type]
            client=_Client(),
            status=None,
        )
    )

    assert nudge is not None
    # Not a stop: the card is not pressed red by a withdrawn request.
    assert nudge.note_code is None
    assert nudge.supersedes_return is True
    # The words still reach the teammate.
    assert "先补测试" in nudge.detail
    # And the old wording is what a red card from this batch would say.
    assert nudge.note_before_supersession == (
        f"{say('prReviewChangesRequested')}（1 条）"
    )
    assert nudge.note == f"{say('prReviewCommented')}（1 条）"


def test_a_request_without_a_timestamp_is_not_superseded_by_another_pers_approval():
    """Both loops have to be equally conservative: a row we cannot order must
    not be cleared by one we can — that errs toward unblocking a real stop."""
    assert superseded_change_requests(
        [
            review(1, "bob", "CHANGES_REQUESTED", ""),
            review(2, "bob", "APPROVED", "2026-01-02T00:00:00Z"),
        ]
    ) == set()


def test_reviews_without_an_author_are_never_the_same_person():
    assert superseded_change_requests(
        [
            review(1, "", "CHANGES_REQUESTED", "2026-01-01T00:00:00Z"),
            review(2, "", "APPROVED", "2026-01-02T00:00:00Z"),
        ]
    ) == set()


def test_a_mixed_batch_keeps_the_live_request_blocking():
    """One withdrawn request and one still-live one: only the withdrawn id is
    matched, so the nudge keeps its stop (and `_review_nudge` keeps the code)."""
    assert superseded_change_requests(
        [
            review(1, "bob", "CHANGES_REQUESTED", "2026-01-01T00:00:00Z"),
            review(2, "bob", "APPROVED", "2026-01-02T00:00:00Z"),
            review(3, "carol", "CHANGES_REQUESTED", "2026-01-03T00:00:00Z"),
        ]
    ) == {"1"}
