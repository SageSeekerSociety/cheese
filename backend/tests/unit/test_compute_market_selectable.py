"""compute_listings / compute_selectable: the local-docker retirement lever (#22 收敛).

Turning ``compute_local_docker_selectable`` off must stop NEW topics from picking
local-docker (drop it from ``compute_selectable``) WITHOUT hiding it from the
catalog (``compute_listings`` still lists it, so a topic already frozen on it keeps
a readable label) and WITHOUT changing how a turn executes.
"""

from types import SimpleNamespace

from app.domain.agent.market import (
    COMPUTE_DEVICE,
    COMPUTE_LOCAL,
    compute_listings,
    compute_selectable,
)


def _settings(*, local_selectable: bool) -> SimpleNamespace:
    # Only the attributes compute_listings reads; device_online is passed
    # explicitly so the live device_hub import path is never taken.
    return SimpleNamespace(
        cheesed_url="",
        compute_provider="local",
        compute_local_docker_selectable=local_selectable,
    )


def test_local_docker_selectable_by_default() -> None:
    s = _settings(local_selectable=True)
    ids = {p.id for p in compute_selectable(s, device_online=True)}
    assert COMPUTE_LOCAL in ids
    assert COMPUTE_DEVICE in ids


def test_retiring_local_docker_drops_it_from_selection_only() -> None:
    s = _settings(local_selectable=False)
    listings = {p.id: p for p in compute_listings(s, device_online=True)}
    # Still in the catalog (locked-topic labels stay readable), marked unavailable.
    assert COMPUTE_LOCAL in listings
    assert listings[COMPUTE_LOCAL].available is False
    # So it is no longer selectable, while an online device still is.
    selectable = {p.id for p in compute_selectable(s, device_online=True)}
    assert COMPUTE_LOCAL not in selectable
    assert COMPUTE_DEVICE in selectable


def test_retiring_local_docker_leaves_empty_picker_without_a_device() -> None:
    # Honesty check: with local-docker retired and no device online the picker is
    # empty — the convergence pressure (#22) is real. A turn still runs because
    # execution falls back to local-docker via compute_default_name and never
    # consults `available`; only NEW selection is gated.
    s = _settings(local_selectable=False)
    assert compute_selectable(s, device_online=False) == []
