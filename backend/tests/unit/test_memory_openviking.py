"""OpenViking memory backend — pure logic (no embedded instance, no network):
scope→user-space mapping, ov.conf generation, backend selection, and the
memory-page URI id codec."""

import json
import uuid

import pytest

from app.api.routes.memory import _decode_uri, _encode_uri
from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.memory.models import MemoryScope
from app.domain.memory.openviking_store import (
    OpenVikingMemoryStore,
    _OpenVikingRuntime,
    forget_uri,
    memories_uri,
    scope_user_id,
)
from app.domain.memory.store import DbMemoryStore, memory_store

# --- scope → OpenViking user space mapping -------------------------------


def test_scope_user_id_uuid_passthrough():
    pid = str(uuid.uuid4())
    assert scope_user_id(MemoryScope.project, pid) == f"project-{pid}"


def test_scope_user_id_handle_passthrough():
    assert scope_user_id(MemoryScope.user, "andyl") == "user-andyl"
    assert scope_user_id(MemoryScope.skill, "data-viz.v2") == "skill-data-viz.v2"


def test_scope_user_id_sanitizes_invalid_chars_collision_free():
    a = scope_user_id(MemoryScope.user, "张三")
    b = scope_user_id(MemoryScope.user, "李四")
    # Only chars from the OpenViking identifier charset survive.
    assert all(c.isalnum() or c in "_.@-" for c in a.removeprefix("user-"))
    # Sanitization must never merge two different ids into one space.
    assert a != b
    # Deterministic: same input, same space.
    assert a == scope_user_id(MemoryScope.user, "张三")


def test_memories_uri_shape():
    assert memories_uri(MemoryScope.user, "andyl") == (
        "viking://user/user-andyl/memories"
    )


# --- ov.conf generation ---------------------------------------------------


def test_write_conf_reflects_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "openviking_data_dir", str(tmp_path / "viking"))
    monkeypatch.setattr(settings, "openviking_llm_model", "test-chat-model")
    monkeypatch.setattr(settings, "openviking_embedding_model", "test-emb-model")
    monkeypatch.setattr(settings, "openviking_llm_api_key", "sk-llm")
    monkeypatch.setattr(settings, "openviking_embedding_api_key", None)
    monkeypatch.setattr(settings, "anthropic_auth_token", "sk-fallback")

    path = _OpenVikingRuntime()._write_conf()
    conf = json.loads(open(path, encoding="utf-8").read())

    assert conf["vlm"]["model"] == "test-chat-model"
    assert conf["vlm"]["api_key"] == "sk-llm"
    # Embedding key falls back to the agent gateway token.
    assert conf["embedding"]["dense"]["api_key"] == "sk-fallback"
    assert conf["embedding"]["dense"]["model"] == "test-emb-model"
    assert conf["storage"]["workspace"].endswith("/data")


# --- backend selection ----------------------------------------------------


def test_factory_defaults_to_db():
    assert isinstance(memory_store(object()), DbMemoryStore)  # type: ignore[arg-type]


def test_factory_selects_openviking(monkeypatch):
    monkeypatch.setattr(settings, "memory_backend", "openviking")
    assert isinstance(memory_store(object()), OpenVikingMemoryStore)  # type: ignore[arg-type]


# --- memory-page id codec -------------------------------------------------


def test_uri_id_roundtrip():
    uri = "viking://user/project-abc/memories/events/决定用PG.md"
    assert _decode_uri(_encode_uri(uri)) == uri


def test_decode_rejects_garbage():
    with pytest.raises(ValidationError):
        _decode_uri("!!!not-base64!!!")


def test_decode_rejects_non_viking_payload():
    with pytest.raises(ValidationError):
        _decode_uri(_encode_uri("https://evil.example/x"))


# --- forget guard ---------------------------------------------------------


@pytest.mark.anyio
async def test_forget_uri_rejects_foreign_uris():
    # Outside CheeseX scope spaces → refused before touching any client.
    for uri in (
        "viking://user/default/memories/events/x.md",  # not a cheesex scope space
        "viking://user/project-abc/sessions/s1",  # not under /memories/
        "viking://resources/whatever",
    ):
        with pytest.raises(ValueError):
            await forget_uri(uri)


# --- listing scopes on the openviking backend -----------------------------


@pytest.mark.anyio
async def test_openviking_listing_covers_a_named_agent_pool(monkeypatch):
    """A named agent's pool is one more scope space, so it IS listable here.

    Sweeping *every* agent pool of a project is not: spaces are isolated with
    no cross-space enumeration. That gap is stated in the route's docstring and
    is why the db backend is the one that answers "all of them".
    """
    from app.api.routes import memory as memory_routes
    from app.domain.memory.openviking_store import OpenVikingMemoryStore

    asked: list[tuple[MemoryScope, str]] = []

    async def _fake_list(self, scope, scope_id, limit=200):
        asked.append((scope, scope_id))
        return [
            {
                "uri": f"{memories_uri(scope, scope_id)}/events/x.md",
                "rel_path": "events/x.md",
                "abstract": f"{scope.value} 记的",
                "mod_time": "2026-01-01T00:00:00+00:00",
            }
        ]

    monkeypatch.setattr(OpenVikingMemoryStore, "list_entries", _fake_list)
    pid = uuid.uuid4()

    items = await memory_routes._list_openviking(pid, None, "ops")
    assert (MemoryScope.agent_project, f"{pid}:ops") in asked
    assert any(e["scope"] == "agent_project" for e in items)

    asked.clear()
    plain = await memory_routes._list_openviking(pid, None, None)
    assert asked == [(MemoryScope.project, str(pid))]
    assert [e["scope"] for e in plain] == ["project"]


# --- the key-less stand-in endpoint --------------------------------------
# Its own logic, not OpenViking's. Both defects below are invisible locally:
# they depend on the order OpenViking happens to build its schema in, which
# is not stable across machines, and each cost a CI round trip.


def test_stand_in_picks_its_memory_type_deterministically():
    from tests.support.fake_model_endpoint import _target_memory_type

    reserved = {"delete_uris"}
    forwards = {"preferences": {}, "events": {}, "delete_uris": {}}
    backwards = {"events": {}, "delete_uris": {}, "preferences": {}}
    assert _target_memory_type(forwards, reserved) == "preferences"
    assert _target_memory_type(backwards, reserved) == "preferences"
    # No preferred type present: still deterministic, never the dict's order.
    assert _target_memory_type({"tools": {}, "soul": {}}, reserved) == "soul"
    assert _target_memory_type({"delete_uris": {}}, reserved) is None


def test_stand_in_writes_message_ranges_openviking_can_parse():
    """`ranges` is parsed with int() per comma-separated part. A bracketed
    value raises there, and extraction then fails with a message about missing
    URIs that says nothing about the real cause."""
    from tests.support.fake_model_endpoint import _SchemaFiller

    value = _SchemaFiller({}, "记住这件事")._string_for("ranges", {})
    assert all(int(part) >= 0 for part in value.split(","))
