"""compute_listings / compute_selectable: the catalog carries only real pools.

Three ids were removed. `local-docker` is retired (#358 "retire local"): it still
EXECUTES — it stays registered in the ComputePool and a topic whose stored profile
names it keeps running there — but it is not listed, not selectable, and no longer
the fallback. `remote-cheesed` and `gpu` went for a different reason: they never
had a provider, a resolution path, or a registration at all, so a permanently
greyed row only taught readers that connecting something would light them up.
"""

from types import SimpleNamespace

from app.domain.agent.market import (
    COMPUTE_CLOUD,
    COMPUTE_DEVICE,
    compute_default_name,
    compute_listings,
    compute_selectable,
)


def _settings() -> SimpleNamespace:
    # Only the attributes compute_listings reads; device_online is passed
    # explicitly so the live device_hub import path is never taken.
    return SimpleNamespace(
        cheesed_url="",
        compute_provider="local",
        microcloud_base_url="",
        microcloud_tenant_secret="",
    )


def test_the_catalog_holds_only_pools_that_exist() -> None:
    ids = {p.id for p in compute_listings(_settings(), device_online=True)}
    # Exact, not a superset: a new listing has to justify itself here, and the
    # platform's own box is deliberately absent — it is what you get by not
    # choosing, never something offered.
    assert ids == {COMPUTE_DEVICE, COMPUTE_CLOUD}
    assert "tmux-hooks" not in ids


def test_cloud_is_where_a_topic_lands_when_nothing_was_selected() -> None:
    # Last selection wins upstream (topic → project sticky → team default); this
    # is the answer when there is none.
    assert compute_default_name() == COMPUTE_CLOUD


def test_the_platforms_own_box_is_never_a_selectable_pool() -> None:
    selectable = {p.id for p in compute_selectable(_settings(), device_online=True)}
    assert "tmux-hooks" not in selectable
    assert COMPUTE_DEVICE in selectable


def test_the_picker_is_honestly_empty_with_nothing_deployed() -> None:
    # No device online and MicroCloud unconfigured: nothing is offered rather than
    # something unusable being offered. A turn still runs — execution never
    # consults `available` — but the picker does not pretend.
    assert compute_selectable(_settings(), device_online=False) == []


def test_cloud_availability_means_provisioning_is_configured() -> None:
    s = _settings()
    s.microcloud_base_url = "https://microcloud.example"
    s.microcloud_tenant_secret = "secret"

    listings = {p.id: p for p in compute_listings(s, device_online=False)}
    assert listings[COMPUTE_CLOUD].available is True
    assert COMPUTE_CLOUD in {p.id for p in compute_selectable(s, device_online=False)}
