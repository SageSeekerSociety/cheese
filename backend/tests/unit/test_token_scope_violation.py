"""A scoped token presented for the WRONG topic/project must 403, never degrade.

Live evidence from 对标 Buzz 找差距: a parent topic used its own scoped token to
post a comment into a CHILD topic, and the comment landed authored by
``anonymous``. The chain: the token's ``t`` claim named the parent, so
``cheese_valid()`` refused it; nothing else identified the caller, so the actor
fell to the Phase-0 ``anonymous`` fallback; ``anonymous`` is *unauthenticated*,
and policy.py lets unauthenticated actors through — so presenting the wrong
token was strictly MORE permissive than presenting none, and the write landed
with its author erased.

These tests pin the rule: a valid-but-out-of-scope token is a scope violation.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import auth as auth_mod
from app.core.errors import AuthenticationRequiredError, ForbiddenError
from app.core.sandbox_auth import (
    SANDBOX_TOKEN,
    bind_resource_token,
    mint_scoped_token,
)

pytestmark = pytest.mark.anyio

PROJECT = uuid.uuid4()
TOPIC = uuid.uuid4()
OTHER_TOPIC = uuid.uuid4()
OTHER_PROJECT = uuid.uuid4()


def _resolver(monkeypatch, *, cheese_token: str):
    monkeypatch.setattr(
        auth_mod,
        "TopicMemberService",
        lambda _session: SimpleNamespace(
            resolve_agent_handle=AsyncMock(side_effect=lambda t: f"cheese-{t.hex[:12]}")
        ),
    )
    monkeypatch.setattr(
        auth_mod,
        "UserRepository",
        lambda _session: SimpleNamespace(
            get_by_id=AsyncMock(return_value=None),
            get_by_username=AsyncMock(return_value=None),
        ),
    )
    return auth_mod.ActorResolver(
        session=MagicMock(scalar=AsyncMock(return_value=None)),
        bearer=None,
        cheese_token=cheese_token,
    )


async def test_token_from_another_topic_is_refused(monkeypatch):
    """The observed bug: parent's token → child topic. Must 403, not anonymous."""
    token = mint_scoped_token(project_id=str(PROJECT), topic_id=str(OTHER_TOPIC))
    resolver = _resolver(monkeypatch, cheese_token=token)

    with pytest.raises(ForbiddenError):
        await resolver.resolve(topic_id=TOPIC, project_id=PROJECT)


async def test_token_from_another_project_is_refused(monkeypatch):
    token = mint_scoped_token(project_id=str(OTHER_PROJECT), topic_id=str(TOPIC))
    resolver = _resolver(monkeypatch, cheese_token=token)

    with pytest.raises(ForbiddenError):
        await resolver.resolve(topic_id=TOPIC, project_id=PROJECT)


async def test_in_scope_token_still_acts_as_this_topics_agent(monkeypatch):
    """The happy path is untouched — and carries the per-topic 分身 handle."""
    token = mint_scoped_token(project_id=str(PROJECT), topic_id=str(TOPIC))
    resolver = _resolver(monkeypatch, cheese_token=token)

    actor = await resolver.resolve(topic_id=TOPIC, project_id=PROJECT)

    assert actor.via == "cheese"
    assert actor.handle != "anonymous"
    assert actor.handle.startswith("cheese-")


async def test_an_expired_sandbox_token_is_still_refused(monkeypatch):
    """The authentication path answers one question — is this credential real —
    and an expired one is not. Pinned separately from the scope tests because
    they fail at a different gate: a mismatched token is refused for naming
    somewhere else, an expired one for having run out, and a change that keeps
    either verdict says nothing about the other."""
    token = mint_scoped_token(project_id=str(PROJECT), topic_id=str(TOPIC), ttl_s=-1)
    resolver = _resolver(monkeypatch, cheese_token=token)

    with pytest.raises(AuthenticationRequiredError):
        await resolver.resolve(topic_id=TOPIC, project_id=PROJECT)


async def test_capability_without_identity_cannot_act_in_a_topic(monkeypatch):
    """Proxy capabilities remain valid at proxies, but cannot become actors."""
    token = mint_scoped_token(project_id=str(PROJECT), topic_id=None)
    resolver = _resolver(monkeypatch, cheese_token=token)

    with pytest.raises(AuthenticationRequiredError):
        await resolver.resolve(topic_id=TOPIC, project_id=PROJECT)


async def test_global_dev_token_keeps_its_existing_behaviour(monkeypatch):
    """The global SANDBOX_TOKEN is not a scoped token; it must not start 403-ing."""
    resolver = _resolver(monkeypatch, cheese_token=SANDBOX_TOKEN)

    actor = await resolver.resolve(topic_id=TOPIC, project_id=PROJECT)

    assert actor.handle == "anonymous"


async def test_no_token_resolves_to_nobody(monkeypatch):
    resolver = _resolver(monkeypatch, cheese_token="")

    actor = await resolver.resolve(topic_id=TOPIC, project_id=PROJECT)

    assert actor.handle == "anonymous"
    assert actor.authenticated is False


async def test_live_session_credential_at_a_route_naming_no_project(monkeypatch):
    """A remote session calling a route that names no project (``GET /projects``)
    with its live, project-scoped credential. The credential still works on every
    project and room route, so answering "invalid or expired" sent the agent off
    to report its platform access as broken. It is a scope refusal: 403."""
    token = bind_resource_token(
        mint_scoped_token(
            project_id=str(PROJECT),
            topic_id=str(TOPIC),
            agent_handle="cedar",
            access_scope="project",
        ),
        "resource-1",
        session_id="session-1",
    )
    resolver = _resolver(monkeypatch, cheese_token=token)

    with pytest.raises(ForbiddenError):
        await resolver.resolve()

    actor = await _resolver(monkeypatch, cheese_token=token).resolve(project_id=PROJECT)
    assert actor.handle == "cedar"


async def test_expired_credential_at_a_route_naming_no_project(monkeypatch):
    """Only a live credential is out of scope; an expired one is still a 401."""
    token = mint_scoped_token(
        project_id=str(PROJECT),
        topic_id=str(TOPIC),
        agent_handle="cedar",
        access_scope="project",
        ttl_s=-1,
    )
    resolver = _resolver(monkeypatch, cheese_token=token)

    with pytest.raises(AuthenticationRequiredError):
        await resolver.resolve()
