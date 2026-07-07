"""Unit tests for the project-member viewer authorization policy.

The two integration adapters (browser auth, membership query) are faked, so the
policy itself — logged-in AND member of the screen's project — is pinned exactly.
The screen carries its own ``project_id``.
"""

import pytest

from app.agent.hub import DeviceHub, HubScreen
from app.agent.viewer_authz import project_member_authorizer

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _FakeDevice:
    async def send_json(self, msg: dict) -> None:  # type: ignore[type-arg]
        pass


async def _screen_in_project(project_id: int) -> HubScreen:
    hub = DeviceHub()
    await hub.attach_device("d", _FakeDevice())
    return await hub.open_screen("d", ["x"], "src", project_id=project_id, agent_user_id=9)


def _authorizer(  # type: ignore[no-untyped-def]
    *,
    user_id: int | None,
    members: set[tuple[int, int]],
    owners: set[tuple[str, int]] | None = None,
    thread_peers: set[tuple[int, int]] | None = None,
):
    async def resolve_user(ws: object) -> int | None:
        return user_id

    async def is_member(project_id: int, uid: int) -> bool:
        return (project_id, uid) in members

    async def is_owner(device_id: str, uid: int) -> bool:
        return (device_id, uid) in (owners or set())

    async def shares_thread(agent_user_id: int, uid: int) -> bool:
        return (agent_user_id, uid) in (thread_peers or set())

    return project_member_authorizer(resolve_user, is_member, is_owner, shares_thread)


async def _screen_no_project() -> HubScreen:
    hub = DeviceHub()
    await hub.attach_device("d", _FakeDevice())
    return await hub.open_screen("d", ["x"], "src", project_id=None, agent_user_id=9)


async def test_member_of_the_project_is_allowed() -> None:
    screen = await _screen_in_project(7)
    authorize = _authorizer(user_id=42, members={(7, 42)})
    assert await authorize(screen, object()) is True  # type: ignore[arg-type]


async def test_non_member_is_denied() -> None:
    screen = await _screen_in_project(7)
    authorize = _authorizer(user_id=42, members={(99, 42)})  # member of a different project
    assert await authorize(screen, object()) is False  # type: ignore[arg-type]


async def test_not_logged_in_is_denied() -> None:
    screen = await _screen_in_project(7)
    authorize = _authorizer(user_id=None, members={(7, 42)})
    assert await authorize(screen, object()) is False  # type: ignore[arg-type]


async def test_project_less_screen_allows_device_owner() -> None:
    screen = await _screen_no_project()
    authorize = _authorizer(user_id=42, members=set(), owners={("d", 42)})
    assert await authorize(screen, object()) is True  # type: ignore[arg-type]


async def test_project_less_screen_denies_non_owner() -> None:
    screen = await _screen_no_project()
    authorize = _authorizer(user_id=7, members=set(), owners={("d", 42)})
    assert await authorize(screen, object()) is False  # type: ignore[arg-type]


async def test_thread_peer_may_watch_any_screen() -> None:
    # Anyone sharing a group with the agent (user 9) may watch, regardless of project.
    screen = await _screen_in_project(7)
    authorize = _authorizer(user_id=42, members=set(), thread_peers={(9, 42)})
    assert await authorize(screen, object()) is True  # type: ignore[arg-type]


async def test_stranger_without_thread_or_project_is_denied() -> None:
    screen = await _screen_no_project()
    authorize = _authorizer(user_id=42, members=set(), owners=set(), thread_peers=set())
    assert await authorize(screen, object()) is False  # type: ignore[arg-type]
