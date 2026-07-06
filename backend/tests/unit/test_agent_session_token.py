"""Unit tests for the agent_session token (mint / decode / rejection)."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.agent.authorization.authorizer import ProjectActor
from app.agent.authorization.token import decode_agent_session, mint_agent_session
from app.core.config import settings
from app.core.errors import AuthenticationRequiredError


def test_roundtrip_agent_actor():
    actor = ProjectActor(kind="agent", actor_id=7, project_id=42, on_behalf_of_user_id=3)
    decoded = decode_agent_session(mint_agent_session(actor))
    assert decoded == actor


def test_roundtrip_user_actor_no_on_behalf():
    actor = ProjectActor(kind="user", actor_id=9, project_id=1)
    decoded = decode_agent_session(mint_agent_session(actor))
    assert decoded == actor
    assert decoded.on_behalf_of_user_id is None


def test_garbage_token_rejected():
    with pytest.raises(AuthenticationRequiredError):
        decode_agent_session("not-a-jwt")


def test_wrong_type_token_rejected():
    # a validly-signed token of the wrong type must not authenticate an actor
    now = datetime.now(UTC)
    token = jwt.encode(
        {"type": "access", "sub": "1", "iat": int(now.timestamp())},
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationRequiredError):
        decode_agent_session(token)


def test_malformed_payload_rejected_not_crash():
    # signed with the real secret but missing actor_id -> clean 401, not a 500
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "type": "agent_session",
            "kind": "agent",
            "project_id": 1,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationRequiredError):
        decode_agent_session(token)
