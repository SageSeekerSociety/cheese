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
    COMPUTE_LOCAL,
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
    assert ids == {COMPUTE_DEVICE, COMPUTE_CLOUD}
    # Named explicitly so a re-added listing has to justify itself here.
    assert COMPUTE_LOCAL not in ids
    assert "remote-cheesed" not in ids and "gpu" not in ids


def test_cloud_is_where_a_topic_lands_when_nothing_was_selected() -> None:
    # Last selection wins upstream (topic → project sticky → team default); this is
    # the answer when there is none. It used to be local-docker, which made the
    # retired pool the destination of every unconfigured topic.
    assert compute_default_name() == COMPUTE_CLOUD


def test_a_retired_pool_cannot_be_selected() -> None:
    selectable = {p.id for p in compute_selectable(_settings(), device_online=True)}
    assert COMPUTE_LOCAL not in selectable
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
