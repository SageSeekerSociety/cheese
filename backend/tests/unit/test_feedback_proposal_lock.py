"""The lock the three proposal limits are serialized behind.

Why a unit test and not one with a database: the thing being pinned is *ordering
inside one request* — the lock is taken before the first read, so a second request
for the same topic cannot read the same "no proposals yet" list and then write.
The integration harness runs every test inside one transaction on one shared
connection (`tests/integration/conftest.py::db_connection`), so two requests that
genuinely overlap cannot be expressed there at all. What is expressible is the
statement order, and that is what this reads.
"""

import uuid

import pytest

from app.domain.feedback import proposals
from app.domain.feedback.schemas import FeedbackProposalIn

TOPIC = uuid.UUID("11111111-2222-3333-4444-555555555555")


class _NoRows:
    """What `check`'s reads produce for a topic with no proposals yet."""

    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return self

    def all(self):
        return []


class _Recorder:
    """A session that records every statement instead of running it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return _NoRows()


async def test_check_takes_the_topic_lock_before_it_reads_anything():
    session = _Recorder()
    body = FeedbackProposalIn(
        title="沙箱里 make 装不上依赖",
        what_happened="make 停在 could not resolve host",
        user_said="用户没有就这个问题说过话",
    )

    fingerprint = await proposals.ProposalService(session).check(TOPIC, body)

    assert fingerprint  # a refusal would have raised instead

    # The lock is the *first* statement of the request — before the dismissal read
    # and before the cards-since-yesterday read. Anywhere later and the read it was
    # meant to order against has already happened, which is the whole bug: two
    # proposals arriving together both read an empty list, and the duplicate check
    # and the daily cap both wave the second one through.
    first, params = session.calls[0]
    assert "pg_advisory_xact_lock" in first
    assert params == {"key": f"feedback-proposal:{TOPIC}"}
    assert len(session.calls) > 1  # …and the reads did happen, after it


async def test_the_lock_key_names_the_topic_and_the_feature():
    """Different topics take different locks, and the key says whose it is.

    Not decoration: advisory locks are one 64-bit space per cluster, shared with
    every other feature that takes one (`agent.execution.lock_release`,
    `machine.warm`). A namespaced string is what keeps two features off one hash.
    """
    keys = []
    for topic in (TOPIC, uuid.uuid4()):
        session = _Recorder()
        body = FeedbackProposalIn(title="t", user_said="s")
        await proposals.ProposalService(session).check(topic, body)
        call = session.calls[0]
        assert "pg_advisory_xact_lock" in call[0]
        keys.append(call[1]["key"])

    assert keys[0] != keys[1]
    assert all(key.startswith("feedback-proposal:") for key in keys)


@pytest.mark.parametrize(
    ("what_happened", "repro"),
    [(None, None), ("", ""), ("   ", None)],
)
def test_a_proposal_with_no_body_text_does_not_share_one_fingerprint(
    what_happened: str | None, repro: str | None
):
    """Two proposals that both leave 「发生了什么 / 怎么复现」 blank are two
    proposals, not one.

    Both fields are optional, so hashing only those two made the fingerprint of
    every such proposal the hash of the empty string — and the second one in a
    topic was then refused with 「这个提案刚提过」, which is a claim about its
    content that the server could not actually make. `title` is required on
    `FeedbackProposalIn`, and it is what the fingerprint falls back to.
    """
    one = proposals.proposal_fingerprint(
        what_happened=what_happened,
        repro=repro,
        title="按钮点了没反应",
        problem="",
    )
    other = proposals.proposal_fingerprint(
        what_happened=what_happened,
        repro=repro,
        title="另一件事",
        problem="",
    )

    assert one != other
    # …and the same words still land together, which is the dedup's actual job.
    assert one == proposals.proposal_fingerprint(
        what_happened=what_happened,
        repro=repro,
        title="按钮点了没反应",
        problem="",
    )
