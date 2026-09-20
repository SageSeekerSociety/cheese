"""Unit tests for the webhook primitive (webhook 原语):
- app.core.webhook_auth: signature/topic-scoped, version-embedding token
- app.domain.webhook.service: verify() against a stored version, post_with_retries()
- app.api.routes.webhooks.receive_webhook: auth failure / validation / source landing

No DB — repositories and sessions are faked.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.routes import webhooks as webhooks_route
from app.core.errors import AuthenticationRequiredError, ValidationError
from app.core.webhook_auth import mint_webhook_token, webhook_token_claims
from app.domain.webhook import service as webhook_service

# ---------------------------------------------------------------------------
# webhook_auth: signature + version-embedding, no TTL
# ---------------------------------------------------------------------------


def test_mint_and_verify_round_trip():
    token = mint_webhook_token(project_id="p1", topic_id="t1", version=3)
    claims = webhook_token_claims(token, topic_id="t1")
    assert claims == {"p": "p1", "t": "t1", "v": 3}


def test_verify_rejects_tampered_signature():
    token = mint_webhook_token(project_id="p1", topic_id="t1", version=1)
    body, _sig = token.split(".", 1)
    tampered = f"{body}.deadbeef"
    assert webhook_token_claims(tampered, topic_id="t1") is None


def test_verify_rejects_token_scoped_to_a_different_topic():
    token = mint_webhook_token(project_id="p1", topic_id="t1", version=1)
    assert webhook_token_claims(token, topic_id="t2") is None


def test_verify_rejects_malformed_token():
    assert webhook_token_claims("not-a-token", topic_id="t1") is None


# ---------------------------------------------------------------------------
# webhook_service.verify(): signature valid but version must also match storage
# ---------------------------------------------------------------------------


class FakeWebhookTokenRepository:
    def __init__(self, session, current: int | None):
        self._current = current

    async def current_version(self, topic_id):
        return self._current


@pytest.mark.anyio
async def test_service_verify_accepts_current_version(monkeypatch):
    topic_id = uuid.uuid4()
    monkeypatch.setattr(
        webhook_service,
        "WebhookTokenRepository",
        lambda session: FakeWebhookTokenRepository(session, current=2),
    )
    token = mint_webhook_token(project_id="p", topic_id=str(topic_id), version=2)
    assert await webhook_service.verify(None, topic_id=topic_id, token=token) is True


@pytest.mark.anyio
async def test_service_verify_rejects_rotated_out_version(monkeypatch):
    """A token minted at version 1, after a rotation bumped storage to 2, must
    stop working — that's the whole point of rotation."""
    topic_id = uuid.uuid4()
    monkeypatch.setattr(
        webhook_service,
        "WebhookTokenRepository",
        lambda session: FakeWebhookTokenRepository(session, current=2),
    )
    token = mint_webhook_token(project_id="p", topic_id=str(topic_id), version=1)
    assert await webhook_service.verify(None, topic_id=topic_id, token=token) is False


@pytest.mark.anyio
async def test_service_verify_rejects_when_no_credential_ever_minted(monkeypatch):
    topic_id = uuid.uuid4()
    monkeypatch.setattr(
        webhook_service,
        "WebhookTokenRepository",
        lambda session: FakeWebhookTokenRepository(session, current=None),
    )
    token = mint_webhook_token(project_id="p", topic_id=str(topic_id), version=1)
    assert await webhook_service.verify(None, topic_id=topic_id, token=token) is False


# ---------------------------------------------------------------------------
# webhook_service.post_with_retries(): source lands in meta, retry-then-give-up
# ---------------------------------------------------------------------------


class FakeSession:
    async def commit(self):
        pass


class FakeSessionCM:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _wire_fake_announce(monkeypatch, *, fail_times: int = 0):
    """post_with_retries owns the retry and the session, `announce` owns the
    write — so this stubs `announce` and watches what the wrapper hands it."""
    sink: list = []
    remaining = {"fail": fail_times}

    async def fake_announce(session, **kwargs):
        if remaining["fail"] > 0:
            remaining["fail"] -= 1
            raise RuntimeError("db unavailable")
        sink.append(kwargs)
        return object()

    monkeypatch.setattr(webhook_service, "announce", fake_announce)
    return sink


@pytest.mark.anyio
async def test_post_with_retries_lands_content_and_source_annotation(monkeypatch):
    sink = _wire_fake_announce(monkeypatch)
    topic_id = uuid.uuid4()

    landed = await webhook_service.post_with_retries(
        lambda: FakeSessionCM(FakeSession()),
        topic_id=topic_id,
        content="build #42 passed",
        source="ci-runner",
    )

    assert landed is True
    assert len(sink) == 1
    call = sink[0]
    assert call["place_id"] == topic_id
    assert call["content"] == "build #42 passed"
    assert call["author"] == "ci-runner"
    assert call["meta"] == {"source": "ci-runner"}


@pytest.mark.anyio
async def test_post_with_retries_succeeds_after_transient_failures(monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_: real_sleep(0))
    sink = _wire_fake_announce(monkeypatch, fail_times=2)

    landed = await webhook_service.post_with_retries(
        lambda: FakeSessionCM(FakeSession()),
        topic_id=uuid.uuid4(),
        content="x",
        source="ci",
    )

    assert landed is True
    assert len(sink) == 1


@pytest.mark.anyio
async def test_post_with_retries_gives_up_after_exhausting_retries(monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_: real_sleep(0))
    sink = _wire_fake_announce(monkeypatch, fail_times=99)

    landed = await webhook_service.post_with_retries(
        lambda: FakeSessionCM(FakeSession()),
        topic_id=uuid.uuid4(),
        content="x",
        source="ci",
    )

    assert landed is False
    assert sink == []


@pytest.mark.anyio
async def test_post_with_retries_does_not_retry_a_room_that_is_gone(monkeypatch):
    """房间没了不是暂时故障 —— 重试三次改变不了这件事，直接放弃。"""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_: real_sleep(0))
    attempts = {"n": 0}

    async def gone(session, **kwargs):
        attempts["n"] += 1
        return None

    monkeypatch.setattr(webhook_service, "announce", gone)

    landed = await webhook_service.post_with_retries(
        lambda: FakeSessionCM(FakeSession()),
        topic_id=uuid.uuid4(),
        content="x",
        source="ci",
    )

    assert landed is False
    assert attempts["n"] == 1


# ---------------------------------------------------------------------------
# route: auth failure / validation / success wiring
# ---------------------------------------------------------------------------


def _request_with_headers(headers: dict) -> SimpleNamespace:
    return SimpleNamespace(headers=headers)


@pytest.mark.anyio
async def test_receive_webhook_rejects_missing_token():
    with pytest.raises(AuthenticationRequiredError):
        await webhooks_route.receive_webhook(
            uuid.uuid4(),
            {"content": "x", "source": "ci"},
            _request_with_headers({}),
            db=None,
        )


@pytest.mark.anyio
async def test_receive_webhook_rejects_invalid_token(monkeypatch):
    monkeypatch.setattr(
        webhooks_route.webhook_service, "verify", AsyncMock(return_value=False)
    )
    request = _request_with_headers({"x-webhook-token": "bad"})
    with pytest.raises(AuthenticationRequiredError):
        await webhooks_route.receive_webhook(
            uuid.uuid4(), {"content": "x", "source": "ci"}, request, db=None
        )


@pytest.mark.anyio
async def test_receive_webhook_rejects_empty_content(monkeypatch):
    monkeypatch.setattr(
        webhooks_route.webhook_service, "verify", AsyncMock(return_value=True)
    )
    request = _request_with_headers({"x-webhook-token": "good"})
    with pytest.raises(ValidationError):
        await webhooks_route.receive_webhook(
            uuid.uuid4(), {"content": "  ", "source": "ci"}, request, db=None
        )


@pytest.mark.anyio
async def test_receive_webhook_lands_the_post_for_a_valid_token(monkeypatch):
    topic_id, project_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(
        webhooks_route.webhook_service, "verify", AsyncMock(return_value=True)
    )
    fake_topic = SimpleNamespace(id=topic_id, project_id=project_id)
    monkeypatch.setattr(
        webhooks_route.TopicRepository,
        "get",
        AsyncMock(return_value=fake_topic),
    )
    post_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(webhooks_route.webhook_service, "post_with_retries", post_mock)

    request = _request_with_headers({"authorization": "Bearer good"})
    result = await webhooks_route.receive_webhook(
        topic_id, {"content": "deployed", "source": "deploy-bot"}, request, db=None
    )

    assert result["data"]["accepted"] is True
    post_mock.assert_awaited_once()
    _, kwargs = post_mock.call_args
    assert kwargs["topic_id"] == topic_id
    assert kwargs["source"] == "deploy-bot"
