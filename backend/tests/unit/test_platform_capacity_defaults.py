"""The platform's own size: how many hosts the cloud pool may hold, how many
free slots it keeps ahead of demand, how soon a green or merged PR reaches its
card, and how much of the installation's GitHub hour the draft sweep may spend.

All of these are deployment defaults — `CLOUD_POOL_MAX_HOSTS`,
`CLOUD_POOL_MIN_FREE_SLOTS`, `ACCEPT_PR_POLL_INTERVAL_S` and the two
`DRAFT_PR_SWEEP_*` settings override them, and deployments are separate — so
what is pinned here is the platform's opinion of its own size until a
deployment says otherwise.

None is a preference. The cap is what the cluster's shared storage has to
hold: ten sandbox hosts provide forty standard sandbox slots, and their disks
written full fill the current 400 GB shared storage pool; each place above ten
over-commits it by a disk, whoever takes it. The cap moves only with the
storage behind it. The
free-slot floor is one host's worth of sandboxes:
below it, the sweep starts creating the next host, so a session arriving then
is placed rather than waiting for one; measured 160 seconds at the median when
it did wait (2026-10-17). The interval is the clock every card's merge state
is mirrored on, hence how late a green or merged PR reaches its card; the rule
that releases an environment counts 「还在等 CI」 off three of them
(`review/queries.CHECK_WATCH_TICKS`).
"""

from app.core.config import settings
from app.domain.machine.models import CloudHost, capacity, warm_pool_target


def test_the_pool_may_hold_twelve_hosts() -> None:
    """Twelve places, whole VMs among them: ten sandbox hosts provide forty
    standard sandbox slots, four to a host, and each place above ten
    over-commits the shared pool by a disk."""
    assert settings.cloud_pool_max_hosts == 12


def test_a_fixed_pool_of_ten_leaves_two_places_for_whole_vms() -> None:
    """The pool keeps ten ordinary hosts up whatever the demand, and the cap of
    twelve leaves the other two places for whole cloud VMs."""
    assert settings.cloud_pool_fixed_hosts == 10
    assert settings.cloud_pool_fixed_hosts + 2 == settings.cloud_pool_max_hosts


def test_a_fixed_pool_needs_no_warm_machines(monkeypatch) -> None:
    """A fixed pool does not prepare machines ahead of demand: it keeps its own
    hosts up, and a warm machine beside them would be one more running and
    billed for nothing."""
    monkeypatch.setattr(settings, "microcloud_warm_pool_size", 2)
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 10)
    assert warm_pool_target() == 0
    monkeypatch.setattr(settings, "cloud_pool_fixed_hosts", 0)
    assert warm_pool_target() == 2


def test_a_host_is_rebuilt_after_two_weeks() -> None:
    """The age fallback of the rolling rebuild: a host of the current image is
    still rolled once it is older than this, so a fleet takes image updates
    and an image omitted from the version does not stick forever."""
    assert settings.cloud_host_rebuild_after_days == 14


def test_the_pool_keeps_a_whole_hosts_slots_free() -> None:
    """The sweep adds a host when the free slots fall below a whole host's
    worth, so the session that arrives after that finds one instead of waiting
    for a host to be created. A host of the deployment's default size runs four
    sandboxes, given the 64 GB data disk its offering gives it."""
    host = CloudHost(
        cores=settings.microcloud_default_cores,
        memory_mb=settings.microcloud_default_memory_mb,
        disk_gb=settings.microcloud_default_disk_gb,
        sandbox_disk_gb=64,
    )
    assert settings.cloud_pool_min_free_slots == capacity(host)


def test_a_hosts_swap_file_is_not_room_for_sandboxes(monkeypatch) -> None:
    """The swap file lives on the data disk the sandboxes share, so it comes
    off the disk before they are placed: turning it off gives the disk back,
    and a deployment that keeps it does not fill the disk it sits on."""
    host = CloudHost(cores=64, memory_mb=65536, disk_gb=100, sandbox_disk_gb=64)
    monkeypatch.setattr(settings, "cloud_host_swap_mb", 0)
    without = capacity(host)
    monkeypatch.setattr(settings, "cloud_host_swap_mb", 8192)

    # 8 GiB of the disk, at the 4 GB one sandbox is expected to take.
    assert capacity(host) == without - 2


def test_a_pressed_pool_keeps_an_idle_sandbox_three_minutes() -> None:
    """While the pool has no free slot (or a session waits for one), an idle
    sandbox on a platform host is destroyed after three minutes instead of ten,
    so the slot goes to whoever is waiting on it (#1829). Long enough to cover
    a session's pause between two tool calls, so a working session does not
    lose its sandbox; short enough that a slot held by a session that stopped
    is back within about one wait (a slow placement measured 160 s at the
    median, 2026-10-17)."""
    pressed = settings.cloud_sandbox_idle_stop_pressed_s
    assert pressed == 180
    assert pressed < settings.cloud_sandbox_idle_stop_s


def test_a_pr_reaches_its_card_within_a_minute() -> None:
    assert settings.accept_pr_poll_interval_s == 60


def test_the_draft_sweep_does_not_spend_the_card_clock() -> None:
    """The draft sweep is the largest single consumer of the installation's
    hourly REST quota: two requests per candidate branch, every pass — 23
    candidates every 60 s measured 2026-10-18, ≈2,760/hour of 5,600. A draft PR
    nobody is waiting for may appear minutes later, so it runs on its own
    slower clock instead of the card poller's, and only for tasks active
    inside a two-hour window."""
    assert settings.draft_pr_sweep_interval_s == 300
    assert settings.draft_pr_sweep_activity_window_s == 7200
