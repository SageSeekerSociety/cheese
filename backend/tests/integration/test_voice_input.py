"""Voice input at the composer (`POST /topics/{id}/voice`), over the real HTTP
stack and database, with the gateway a MockTransport.

What is pinned: a recording comes back as tidy text and as what was heard; the
speech model hears it with the room's names as hotwords; both calls are
recorded against the project under their own kinds at the gateway's price, and
cost no credits while voice input is free; a failed tidy still hands back what
was heard; a person's daily seconds run out and further recordings are refused
before anything is sent upstream; something that is not a WAV recording is
refused before anything is sent upstream; someone outside the project cannot
use a room's microphone.
"""

import asyncio
import io
import json
import uuid
import wave

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.domain.usage.models import ResourceUsage
from tests.conftest import seed_user
from tests.integration.conftest import post_project


def _wav(seconds: float, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> dict:
    """``polish`` is what the tidying model answers, or an ``httpx.Response``."""
    seen: dict = {"asr": [], "polish_asked": [], "polish": "@芝士 帮我看一下这段。"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/key/generate":
            alias = json.loads(request.content).get("key_alias")
            return httpx.Response(200, json={"key": f"sk-{alias}"})
        if request.url.path == "/key/delete":
            return httpx.Response(200, json={})
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            if body["model"] == settings.voice_asr_model:
                seen["asr"].append(body)
                return httpx.Response(
                    200,
                    headers={"x-litellm-response-cost": "0.000124"},
                    json={
                        "choices": [
                            {"message": {"content": "嗯那个艾特芝士 帮我看一下这段"}}
                        ],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 0},
                    },
                )
            seen["polish_asked"].append(body["messages"][-1]["content"])
            if isinstance(seen["polish"], httpx.Response):
                return seen["polish"]
            return httpx.Response(
                200,
                headers={"x-litellm-response-cost": "0.00002"},
                json={
                    "choices": [{"message": {"content": seen["polish"]}}],
                    "usage": {"prompt_tokens": 200, "completion_tokens": 20},
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    class Stubbed(real):
        def __init__(self, *args, **kwargs):
            if kwargs.get("transport") is None:
                kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Stubbed)
    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway")
    monkeypatch.setattr(settings, "llm_gateway_admin_key", "sk-master")
    return seen


@pytest.fixture
def alice(client) -> dict[str, str]:
    return {"Authorization": f"Bearer {seed_user(client, 'alice')}"}


def _room(client, alice) -> dict:
    r = post_project(client, json={"name": "P"}, headers=alice, owner="alice")
    assert r.status_code == 200, r.text
    project = r.json()["data"]
    return {"project_id": project["id"], "room_id": project["root_topic_id"]}


def _speak(client, headers, room_id: str, audio: bytes, name: str = "voice.wav"):
    return client.post(
        f"/topics/{room_id}/voice",
        files={"file": (name, audio, "audio/wav")},
        headers=headers,
    )


def _usage(client, project_id: str) -> list[ResourceUsage]:
    async def read():
        async with client.test_factory() as s:
            rows = await s.scalars(
                select(ResourceUsage).where(
                    ResourceUsage.project_id == uuid.UUID(project_id),
                    ResourceUsage.kind.in_(("voice_asr", "voice_polish")),
                )
            )
            return list(rows)

    return asyncio.run(read())


def test_a_recording_comes_back_tidy_and_as_heard(client, gateway, alice):
    place = _room(client, alice)
    r = _speak(client, alice, place["room_id"], _wav(3.0))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["text"] == "@芝士 帮我看一下这段。"
    assert data["raw"] == "嗯那个艾特芝士 帮我看一下这段"
    assert data["seconds"] == 3.0
    assert data["remaining_seconds"] == settings.voice_daily_seconds - 3

    (asked,) = gateway["asr"]
    audio = asked["messages"][-1]["content"][0]["input_audio"]["data"]
    assert audio.startswith("data:audio/wav;base64,")
    # The room's people are heard as hotwords, and named in the context.
    assert any("alice" in word for word in asked["vocabulary"]), asked["vocabulary"]
    assert "alice" in asked["messages"][0]["content"]
    # The tidying model is given what was heard.
    assert "嗯那个艾特芝士 帮我看一下这段" in gateway["polish_asked"][0]


def test_both_calls_are_recorded_and_free_while_voice_is_free(
    client, gateway, alice, monkeypatch
):
    monkeypatch.setattr(settings, "voice_input_charged", False)
    place = _room(client, alice)
    assert _speak(client, alice, place["room_id"], _wav(2.0)).status_code == 200
    rows = {row.kind: row for row in _usage(client, place["project_id"])}
    assert set(rows) == {"voice_asr", "voice_polish"}
    assert rows["voice_asr"].cost_usd == pytest.approx(0.000124)
    assert rows["voice_asr"].input_tokens == 100
    assert rows["voice_polish"].cost_usd == pytest.approx(0.00002)
    assert all(row.credits == 0 for row in rows.values())


def test_charging_turned_on_costs_the_project_credits(
    client, gateway, alice, monkeypatch
):
    monkeypatch.setattr(settings, "voice_input_charged", True)
    place = _room(client, alice)
    assert _speak(client, alice, place["room_id"], _wav(2.0)).status_code == 200
    assert all(row.credits > 0 for row in _usage(client, place["project_id"]))


def test_a_failed_tidy_hands_back_what_was_heard(client, gateway, alice):
    gateway["polish"] = httpx.Response(500, json={"error": "down"})
    place = _room(client, alice)
    r = _speak(client, alice, place["room_id"], _wav(1.5))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["text"] == data["raw"] == "嗯那个艾特芝士 帮我看一下这段"


def test_the_daily_allowance_runs_out(client, gateway, alice, monkeypatch):
    monkeypatch.setattr(settings, "voice_daily_seconds", 5)
    place = _room(client, alice)
    assert _speak(client, alice, place["room_id"], _wav(3.0)).status_code == 200
    r = _speak(client, alice, place["room_id"], _wav(3.0))
    assert r.status_code == 429, r.text
    assert len(gateway["asr"]) == 1, "a refused recording must not be sent upstream"


def test_a_recording_too_long_is_refused(client, gateway, alice, monkeypatch):
    monkeypatch.setattr(settings, "voice_max_seconds", 2)
    place = _room(client, alice)
    r = _speak(client, alice, place["room_id"], _wav(4.0))
    assert r.status_code == 422, r.text
    assert gateway["asr"] == []


def test_something_that_is_not_a_wav_is_refused(client, gateway, alice):
    place = _room(client, alice)
    r = _speak(client, alice, place["room_id"], b"OggS not a wav", name="voice.ogg")
    assert r.status_code == 422, r.text
    assert gateway["asr"] == []


def test_someone_outside_the_project_cannot_speak_into_its_room(client, gateway, alice):
    place = _room(client, alice)
    bob = {"Authorization": f"Bearer {seed_user(client, 'bob')}"}
    r = _speak(client, bob, place["room_id"], _wav(1.0))
    assert r.status_code in (403, 404), r.text
    assert gateway["asr"] == []


def test_said_to_ones_own_cheese_it_is_in_no_project_and_shares_the_day(
    client, gateway, alice, monkeypatch
):
    """Speaking to one's own 芝士 needs no room; it is billed to nobody's
    project, and it counts against the same daily seconds as a room's."""
    monkeypatch.setattr(settings, "voice_daily_seconds", 5)
    place = _room(client, alice)
    assert _speak(client, alice, place["room_id"], _wav(3.0)).status_code == 200

    r = client.post(
        "/assistant/voice",
        files={"file": ("voice.wav", _wav(1.5), "audio/wav")},
        headers=alice,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["text"] == "@芝士 帮我看一下这段。"
    assert r.json()["data"]["remaining_seconds"] == 0

    async def outside_projects():
        async with client.test_factory() as s:
            return list(
                await s.scalars(
                    select(ResourceUsage.kind).where(
                        ResourceUsage.project_id.is_(None),
                        ResourceUsage.kind.in_(("voice_asr", "voice_polish")),
                    )
                )
            )

    assert sorted(asyncio.run(outside_projects())) == ["voice_asr", "voice_polish"]
    over = client.post(
        "/assistant/voice",
        files={"file": ("voice.wav", _wav(1.0), "audio/wav")},
        headers=alice,
    )
    assert over.status_code == 429, over.text
