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
