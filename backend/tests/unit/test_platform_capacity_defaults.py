"""The platform's own size: how many hosts the cloud pool may hold, and how
soon a green or merged PR reaches its card.

Both are deployment defaults — `CLOUD_POOL_MAX_HOSTS` and
`ACCEPT_PR_POLL_INTERVAL_S` override them, and deployments are separate — so
what is pinned here is the platform's opinion of its own size until a
deployment says otherwise.

Neither is a preference. The cap is what the cluster's shared storage has to
hold: thirty hosts' disks written full are 1.2 TB (`docs/microcloud.md`), so it
moves only with the storage behind it. The interval is the clock every card's
merge state is mirrored on, hence how late a green or merged PR reaches its
card; the rule that releases an environment counts 「还在等 CI」 off three of
them (`review/queries.CHECK_WATCH_TICKS`).
"""

from app.core.config import settings


def test_the_pool_may_hold_thirty_hosts() -> None:
    """A hundred and twenty sandbox slots, four to a host: where the 30+
    parallel lines of the sprint put their sandboxes."""
    assert settings.cloud_pool_max_hosts == 30


def test_a_pr_reaches_its_card_within_a_minute() -> None:
    assert settings.accept_pr_poll_interval_s == 60
