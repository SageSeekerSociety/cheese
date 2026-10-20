"""The stale-progress reminder reads which cards are still open from the
cards' own table, and must agree with the review domain about which statuses
those are: a status open there and missing here would have the teammate asked
for a report the task progress route then refuses."""

from app.domain.agent.turn.steps.reminders import OPEN_CARD_STATUSES
from app.domain.review.archive import OPEN_CARD_STATUSES as REVIEWS


def test_the_reminder_knows_every_open_card_status():
    assert set(OPEN_CARD_STATUSES) == {str(status) for status in REVIEWS}
