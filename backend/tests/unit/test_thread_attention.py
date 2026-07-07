"""Unit tests for the chat attention policy: its string encoding round-trip and the
``set_attention`` / ``get_attention`` service ops that persist and read it back.

No DB: the session factory is a fake async context manager and the repositories /
identity helpers on ``app.domain.thread.services`` are monkeypatched with stateful
in-memory fakes, so a ``set_attention`` genuinely round-trips through ``get_attention``.
"""

from types import SimpleNamespace, TracebackType
from typing import cast
from unittest.mock import MagicMock

import pytest

from app.core.errors import BadRequestError
from app.domain.thread import services as thread_services
from app.domain.thread.services import (
    ThreadService,
    format_attention_policy,
    parse_attention_policy,
)

pytestmark = pytest.mark.anyio

ACTOR_ID = 100
THREAD_ID = 42
AGENT_ID = 7


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Encoding round-trip (pure functions)
# ---------------------------------------------------------------------------


def test_encode_all_round_trip() -> None:
    assert parse_attention_policy(format_attention_policy("ALL", None)) == ("ALL", None)


def test_encode_mention_round_trip() -> None:
    assert parse_attention_policy(format_attention_policy("MENTION", None)) == ("MENTION", None)


def test_encode_interval_round_trip() -> None:
    encoded = format_attention_policy("INTERVAL", 15)
    assert encoded == "INTERVAL:15"
    assert parse_attention_policy(encoded) == ("INTERVAL", 15)


def test_null_override_reads_as_mention_default() -> None:
    assert parse_attention_policy(None) == ("MENTION", None)


def test_unknown_override_reads_as_mention_default() -> None:
    assert parse_attention_policy("garbage") == ("MENTION", None)


def test_format_rejects_bad_mode() -> None:
    with pytest.raises(BadRequestError):
        format_attention_policy("SOMETIMES", None)


def test_format_rejects_interval_without_minutes() -> None:
    with pytest.raises(BadRequestError):
        format_attention_policy("INTERVAL", None)


# ---------------------------------------------------------------------------
# Stateful fakes for the service round-trip
# ---------------------------------------------------------------------------


class _FakeSession:
    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        return None


class _FakeMembershipRepo:
    """One shared in-memory membership store, ignoring the (fake) session."""

    def __init__(self, store: dict[int, str | None]) -> None:
        self._store = store

    async def is_member(self, thread_id: int, user_id: int) -> bool:
        return user_id in self._store or user_id == ACTOR_ID

    async def role_of(self, thread_id: int, user_id: int) -> int:
        return 2  # OWNER — satisfies any _require_* check

    async def set_attention(self, thread_id: int, user_id: int, value: str | None) -> None:
        self._store[user_id] = value

    async def members(self, thread_id: int) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(user_id=uid, role=0, attention_policy_override=raw)
            for uid, raw in self._store.items()
        ]


def _make_service(
    monkeypatch: pytest.MonkeyPatch, store: dict[int, str | None]
) -> ThreadService:
    repo = _FakeMembershipRepo(store)
    monkeypatch.setattr(thread_services, "ThreadMembershipRepository", lambda _s: repo)
    monkeypatch.setattr(
        thread_services, "ThreadRepository", lambda _s: SimpleNamespace(get=_get_thread)
    )

    async def _resolve(_session: object, _hub: object, ids: list[int]) -> set[int]:
        return {uid for uid in ids if uid == AGENT_ID}

    async def _dicts(
        _session: object, _hub: object, ids: list[int], roles: object = None
    ) -> list[dict[str, object]]:
        return [
            {"user_id": uid, "nickname": f"agent{uid}", "avatar_id": 1} for uid in ids
        ]

    monkeypatch.setattr(thread_services, "resolve_agent_identities", _resolve)
    monkeypatch.setattr(thread_services, "build_member_dicts", _dicts)

    session_factory = cast(object, lambda: _FakeSession())
    return ThreadService(session_factory, MagicMock(), MagicMock())  # type: ignore[arg-type]


async def _get_thread(thread_id: int) -> SimpleNamespace:
    return SimpleNamespace(id=thread_id, title="t", project_id=None)


# ---------------------------------------------------------------------------
# set_attention → get_attention round-trip
# ---------------------------------------------------------------------------


async def test_set_all_persists_and_reads_back(monkeypatch: pytest.MonkeyPatch) -> None:
    store: dict[int, str | None] = {AGENT_ID: None}
    svc = _make_service(monkeypatch, store)

    out = await svc.set_attention(ACTOR_ID, THREAD_ID, AGENT_ID, "ALL", None)
    assert out["agent"] == {  # type: ignore[comparison-overlap]
        "user_id": AGENT_ID,
        "nickname": f"agent{AGENT_ID}",
        "avatar_id": 1,
        "mode": "ALL",
    }
    assert store[AGENT_ID] == "ALL"

    read = await svc.get_attention(ACTOR_ID, THREAD_ID)
    agents = cast(list[dict[str, object]], read["agents"])
    assert agents == [
        {"user_id": AGENT_ID, "nickname": f"agent{AGENT_ID}", "avatar_id": 1, "mode": "ALL"}
    ]


async def test_set_interval_persists_and_reads_back(monkeypatch: pytest.MonkeyPatch) -> None:
    store: dict[int, str | None] = {AGENT_ID: None}
    svc = _make_service(monkeypatch, store)

    out = await svc.set_attention(ACTOR_ID, THREAD_ID, AGENT_ID, "INTERVAL", 20)
    agent = cast(dict[str, object], out["agent"])
    assert agent["mode"] == "INTERVAL"
    assert agent["interval_minutes"] == 20
    assert store[AGENT_ID] == "INTERVAL:20"

    read = await svc.get_attention(ACTOR_ID, THREAD_ID)
    agents = cast(list[dict[str, object]], read["agents"])
    assert agents[0]["mode"] == "INTERVAL"
    assert agents[0]["interval_minutes"] == 20


async def test_set_mention_persists_and_reads_back(monkeypatch: pytest.MonkeyPatch) -> None:
    store: dict[int, str | None] = {AGENT_ID: "ALL"}
    svc = _make_service(monkeypatch, store)

    await svc.set_attention(ACTOR_ID, THREAD_ID, AGENT_ID, "MENTION", None)
    assert store[AGENT_ID] == "MENTION"

    read = await svc.get_attention(ACTOR_ID, THREAD_ID)
    agents = cast(list[dict[str, object]], read["agents"])
    assert agents[0]["mode"] == "MENTION"
    assert "interval_minutes" not in agents[0]


async def test_null_override_reads_as_mention_default_via_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store: dict[int, str | None] = {AGENT_ID: None}
    svc = _make_service(monkeypatch, store)

    read = await svc.get_attention(ACTOR_ID, THREAD_ID)
    agents = cast(list[dict[str, object]], read["agents"])
    assert agents[0]["mode"] == "MENTION"
    assert "interval_minutes" not in agents[0]
