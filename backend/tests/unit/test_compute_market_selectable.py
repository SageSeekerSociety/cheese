"""The 市场 lists the sandbox specs there are, and only those.

A spec of the platform's machines is available where this deployment can
provision them; people's own computers are available once one is connected.
"""

from types import SimpleNamespace

from app.domain.agent.market import compute_listings


def _settings(*, cloud: bool, vm: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        microcloud_base_url="https://cloud.example" if cloud else "",
        microcloud_tenant_secret="secret" if cloud else "",
        microcloud_vm_offering_id=2 if vm else 0,
    )


def _available(settings, *, device_online: bool) -> set[str]:
    return {
        listing.id
        for listing in compute_listings(settings, device_online=device_online)
        if listing.available
    }


def test_nothing_is_available_with_nothing_deployed_or_connected() -> None:
    assert _available(_settings(cloud=False), device_online=False) == set()


def test_the_platforms_specs_follow_what_it_can_provision() -> None:
    assert _available(_settings(cloud=True), device_online=False) == {"standard"}
    assert _available(_settings(cloud=True, vm=True), device_online=False) == {
        "standard",
        "vm",
    }


def test_peoples_computers_are_available_once_one_is_connected() -> None:
    assert _available(_settings(cloud=False), device_online=True) == {"devices_of"}
