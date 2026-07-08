"""Unit tests for the block substrate's message plumbing (万物皆块 §2.1).

Two behaviours, no DB (all repositories/services mocked with SimpleNamespace + mocks):

- ``ThreadService.list_messages`` — enforces thread membership and returns the rows
  ``BlockRepository.messages_since`` yields, shaped into message JSON. This is the
  service-layer proof of the ``messages_since(thread_id, after_id)`` contract
  (MESSAGE-kind, ``id > after_id``, ascending) since there's no DB to run the query.
- The agent tool door's ``post-note`` handler — with a resolved in-screen agent user it
  writes a MESSAGE block authored by that agent and returns ``{ok, id, thread_id}``;
  it falls back to the agent's last-@'d thread, 400s with no target, 404s on an unknown
  thread. Auth 401 paths live in ``test_project_chat.py`` and are not duplicated here.
"""

from datetime import UTC, datetime
from types import SimpleNamespace, TracebackType
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import app.api.routes.agent_api as agent_api
import app.domain.thread.services as thread_services
from app.agent.hub import DeviceHub
from app.api.routes.agent_api import build_agent_api
from app.core.errors import ForbiddenError, NotFoundError
from app.domain.thread.services import ThreadService

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --------------------------------------------------------------------------- #
# A fake async-session context manager: `async with session_factory() as s:`   #
# --------------------------------------------------------------------------- #
class _FakeSessionCM:
    def __init__(self, session: object) -> None:
        self._session = session

    async def __aenter__(self) -> object:
        return self._session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


def _session_factory(session: object | None = None) -> MagicMock:
    """A callable that, when called, yields `session` from an async context manager."""
    factory = MagicMock()
    factory.return_value = _FakeSessionCM(session if session is not None else object())
    return factory


def _block(
    *, block_id: int, thread_id: int = 5, author_id: int = 42, text: str = "hi"
) -> SimpleNamespace:
    return SimpleNamespace(
        id=block_id,
        thread_id=thread_id,
        author_id=author_id,
        content=text,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        reply_to_id=None,
        deleted_at=None,
        pinned_at=None,
    )


# --------------------------------------------------------------------------- #
# ThreadService.list_messages                                                  #
# --------------------------------------------------------------------------- #
async def test_list_messages_returns_repo_rows_shaped(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_block(block_id=11, text="a"), _block(block_id=12, text="b")]
    block_repo = SimpleNamespace(messages_since=AsyncMock(return_value=rows))
    thread_repo = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id=5, project_id=None)))
    membership_repo = SimpleNamespace(is_member=AsyncMock(return_value=True))

    monkeypatch.setattr(thread_services, "BlockRepository", lambda _s: block_repo)
    monkeypatch.setattr(thread_services, "ThreadRepository", lambda _s: thread_repo)
    monkeypatch.setattr(thread_services, "ThreadMembershipRepository", lambda _s: membership_repo)
    monkeypatch.setattr(
        thread_services,
        "build_member_dicts",
        AsyncMock(return_value=[{"user_id": 42, "nickname": "bot", "role": "x"}]),
    )

    svc = ThreadService(_session_factory(), DeviceHub(), SimpleNamespace())
    result = await svc.list_messages(actor_id=42, thread_id=5, after=10)

    # Exactly the repo's rows, in order, shaped into message JSON.
    messages = result["messages"]
    assert isinstance(messages, list)
    assert [m["id"] for m in messages] == [11, 12]
    assert [m["text"] for m in messages] == ["a", "b"]
    # The service passes (thread_id, after) straight through to the repo contract.
    block_repo.messages_since.assert_awaited_once_with(5, 10)


async def test_list_messages_defaults_after_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    block_repo = SimpleNamespace(messages_since=AsyncMock(return_value=[]))
    monkeypatch.setattr(thread_services, "BlockRepository", lambda _s: block_repo)
    monkeypatch.setattr(
        thread_services,
        "ThreadRepository",
        lambda _s: SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id=5))),
    )
    monkeypatch.setattr(
        thread_services,
        "ThreadMembershipRepository",
        lambda _s: SimpleNamespace(is_member=AsyncMock(return_value=True)),
    )

    svc = ThreadService(_session_factory(), DeviceHub(), SimpleNamespace())
    result = await svc.list_messages(actor_id=42, thread_id=5)

    assert result["messages"] == []
    block_repo.messages_since.assert_awaited_once_with(5, 0)


async def test_list_messages_requires_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    block_repo = SimpleNamespace(messages_since=AsyncMock(return_value=[_block(block_id=1)]))
    monkeypatch.setattr(thread_services, "BlockRepository", lambda _s: block_repo)
    monkeypatch.setattr(
        thread_services,
        "ThreadRepository",
        lambda _s: SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id=5))),
    )
    monkeypatch.setattr(
        thread_services,
        "ThreadMembershipRepository",
        lambda _s: SimpleNamespace(is_member=AsyncMock(return_value=False)),
    )

    svc = ThreadService(_session_factory(), DeviceHub(), SimpleNamespace())
    with pytest.raises(ForbiddenError):
        await svc.list_messages(actor_id=99, thread_id=5)
    # Membership is enforced before any message is read.
    block_repo.messages_since.assert_not_awaited()


async def test_list_messages_unknown_thread_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        thread_services,
        "ThreadRepository",
        lambda _s: SimpleNamespace(get=AsyncMock(return_value=None)),
    )
    monkeypatch.setattr(
        thread_services,
        "ThreadMembershipRepository",
        lambda _s: SimpleNamespace(is_member=AsyncMock(return_value=True)),
    )

    svc = ThreadService(_session_factory(), DeviceHub(), SimpleNamespace())
    with pytest.raises(NotFoundError):
        await svc.list_messages(actor_id=42, thread_id=404)


# --------------------------------------------------------------------------- #
# Agent tool door: post-note (happy path + thread routing)                     #
# --------------------------------------------------------------------------- #
_AGENT_UID = 42
_HEADERS = {"Authorization": "Bearer dev-token", "X-Cheese-Screen": "screen-secret"}


def _wire_agent_door(
    monkeypatch: pytest.MonkeyPatch,
    *,
    post_result: dict[str, object] | None = None,
    post_error: Exception | None = None,
    last_thread: int | None = None,
) -> tuple[TestClient, AsyncMock, MagicMock]:
    """Build a post-note client whose auth resolves to an in-screen agent user. post-note
    now delegates to ``ThreadService.post_message`` (the SAME path as a human message, so
    @-mentions forward), so we mock that instead of the old direct block write."""
    monkeypatch.setattr(
        agent_api,
        "resolve_actor",
        AsyncMock(return_value=SimpleNamespace(inside_screen=True, actor_user_id=_AGENT_UID)),
    )
    post_message = AsyncMock(
        side_effect=post_error,
        return_value=post_result or {"message": {"id": 777, "thread_id": 5}, "forwarded_to_agents": 0},
    )
    monkeypatch.setattr(agent_api, "ThreadService", lambda *a, **k: SimpleNamespace(post_message=post_message))

    agent_service = SimpleNamespace(last_thread_for_agent=MagicMock(return_value=last_thread))
    device_service = SimpleNamespace()
    api = build_agent_api(
        _session_factory(SimpleNamespace(commit=AsyncMock())),  # type: ignore[arg-type]
        agent_service,  # type: ignore[arg-type]
        DeviceHub(),
        device_service,  # type: ignore[arg-type]
    )
    return TestClient(api), post_message, agent_service.last_thread_for_agent


async def test_post_note_delegates_to_post_message(monkeypatch: pytest.MonkeyPatch) -> None:
    client, post_message, _ = _wire_agent_door(
        monkeypatch, post_result={"message": {"id": 777, "thread_id": 5}, "forwarded_to_agents": 2}
    )
    r = client.post("/notes", json={"text": "hello thread", "thread_id": 5}, headers=_HEADERS)

    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": 777, "thread_id": 5, "forwarded_to_agents": 2}
    # Goes through the unified pipeline (mention scan + forward), authored by the agent.
    post_message.assert_awaited_once_with(actor_id=_AGENT_UID, thread_id=5, text="hello thread")


async def test_post_note_falls_back_to_last_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    client, post_message, last_thread = _wire_agent_door(
        monkeypatch, post_result={"message": {"id": 1, "thread_id": 9}}, last_thread=9
    )
    r = client.post("/notes", json={"text": "no explicit thread"}, headers=_HEADERS)

    assert r.status_code == 200
    assert r.json()["thread_id"] == 9
    last_thread.assert_called_once_with(_AGENT_UID)
    assert post_message.await_args.kwargs["thread_id"] == 9


async def test_post_note_no_target_thread_is_bad_request(monkeypatch: pytest.MonkeyPatch) -> None:
    client, post_message, _ = _wire_agent_door(monkeypatch, last_thread=None)
    r = client.post("/notes", json={"text": "orphan"}, headers=_HEADERS)

    assert r.status_code == 400
    post_message.assert_not_awaited()


async def test_post_note_unknown_thread_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    # A resolved thread that post_message rejects (not a member / missing) → 404 surfaces.
    client, post_message, _ = _wire_agent_door(
        monkeypatch, post_error=NotFoundError("Unknown thread"), last_thread=123
    )
    r = client.post("/notes", json={"text": "into the void"}, headers=_HEADERS)

    assert r.status_code == 404
    post_message.assert_awaited_once()
