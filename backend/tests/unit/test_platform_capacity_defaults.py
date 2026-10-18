"""The platform's own size: how many hosts the cloud pool may hold, how many
free slots it keeps ahead of demand, and how soon a green or merged PR reaches
its card.

All three are deployment defaults — `CLOUD_POOL_MAX_HOSTS`,
`CLOUD_POOL_MIN_FREE_SLOTS` and `ACCEPT_PR_POLL_INTERVAL_S` override them, and
deployments are separate — so what is pinned here is the platform's opinion of
its own size until a deployment says otherwise.

None is a preference. The cap is what the cluster's shared storage has to
hold: ten sandbox hosts provide forty standard sandbox slots, and their disks
written full fill the current 400 GB shared storage pool; the two further
places are whole cloud VMs, which count like hosts and over-commit that pool
by their own disks. The cap moves only with the storage behind it. The
free-slot floor is one host's worth of sandboxes:
below it, the sweep starts creating the next host, so a session arriving then
is placed rather than waiting for one; measured 160 seconds at the median when
it did wait (2026-10-17). The interval is the clock every card's merge state
is mirrored on, hence how late a green or merged PR reaches its card; the rule
that releases an environment counts 「还在等 CI」 off three of them
(`review/queries.CHECK_WATCH_TICKS`).
"""

from app.core.config import settings
from app.domain.machine.models import CloudHost, capacity


def test_the_pool_may_hold_twelve_hosts() -> None:
    """Twelve places, whole VMs among them: ten sandbox hosts provide forty
    standard sandbox slots, four to a host, and two more places are for VMs."""
    assert settings.cloud_pool_max_hosts == 12


def test_the_pool_keeps_a_whole_hosts_slots_free() -> None:
    """The sweep adds a host when the free slots fall below a whole host's
    worth, so the session that arrives after that finds one instead of waiting
    for a host to be created. A host of the deployment's default size runs four
    sandboxes."""
    host = CloudHost(
        cores=settings.microcloud_default_cores,
        memory_mb=settings.microcloud_default_memory_mb,
        disk_gb=settings.microcloud_default_disk_gb,
    )
    assert settings.cloud_pool_min_free_slots == capacity(host)


def test_a_pr_reaches_its_card_within_a_minute() -> None:
    assert settings.accept_pr_poll_interval_s == 60
