"""compute_listings / compute_selectable: the catalog carries only real pools.

Three ids were removed. `local-docker` is retired (#358 "retire local") — not
listed, not selectable, not the fallback, and no longer in the ComputePool
either, so there is nowhere left for an unconfigured topic to land on it.
`remote-cheesed` and `gpu` went for a different reason: they never had a
provider, a resolution path, or a registration at all, so a permanently greyed
row only taught readers that connecting something would light them up.
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


def test_the_default_names_a_pool_this_deployment_actually_has() -> None:
    """Last selection wins upstream (topic → project sticky → team default); this
    is the answer when there is none, and it has to be a machine that exists.

    Cloud where the deployment can provision one, the self-hosted pool where it
    cannot — never a name that resolves to nothing, because this same answer is
    what `build_compute_pool` hands an unconfigured turn."""
    s = _settings()
    assert compute_default_name(s) == COMPUTE_DEVICE

    s.microcloud_base_url = "https://microcloud.example"
    s.microcloud_tenant_secret = "secret"
    assert compute_default_name(s) == COMPUTE_CLOUD


def test_the_row_marked_default_is_the_one_a_turn_lands_on() -> None:
    """The catalogue and the executor read one function, so the 默认 badge cannot
    point at a pool other than the one an unconfigured topic runs on — which is
    exactly what it used to do."""
    for cloud_configured in (False, True):
        s = _settings()
        if cloud_configured:
            s.microcloud_base_url = "https://microcloud.example"
            s.microcloud_tenant_secret = "secret"
        marked = [p.id for p in compute_listings(s, device_online=True) if p.default]
        assert marked == [compute_default_name(s)]


def test_only_a_pool_that_can_run_right_now_is_selectable() -> None:
    selectable = {p.id for p in compute_selectable(_settings(), device_online=True)}
    assert selectable == {COMPUTE_DEVICE}  # a device is online, Cloud is not configured


def test_the_picker_is_honestly_empty_with_nothing_deployed() -> None:
    # No device online and MicroCloud unconfigured: nothing is offered rather than
    # something unusable being offered. A turn started anyway fails saying no
    # machine is connected, which is the same answer the empty picker gives.
    assert compute_selectable(_settings(), device_online=False) == []


def test_cloud_availability_means_provisioning_is_configured() -> None:
    s = _settings()
    s.microcloud_base_url = "https://microcloud.example"
    s.microcloud_tenant_secret = "secret"

    listings = {p.id: p for p in compute_listings(s, device_online=False)}
    assert listings[COMPUTE_CLOUD].available is True
    assert COMPUTE_CLOUD in {p.id for p in compute_selectable(s, device_online=False)}
