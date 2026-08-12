"""A member's own agent: how the credential resolves, and what it may reach.

Both seams under test are pure (no DB, no request), which is the point — the
rule that an agent may do exactly what its owner may do has to be readable and
checkable on its own, not inferred from wiring.
"""

import uuid

import pytest

from app.domain.authz.policy import authorize_topic_access
from app.domain.identity.actor import (
    Actor,
    DelegatedIdentity,
    TokenIdentity,
    resolve_actor,
)

pytestmark = pytest.mark.anyio

PROJECT = uuid.uuid4()
TOPIC = uuid.uuid4()


async def _no_cheese() -> bool:
    return False


async def _never_agent(handle: str) -> bool:
    return False


def _delegated_verifier(expected: str, identity: DelegatedIdentity):
    async def verify(secret: str) -> DelegatedIdentity | None:
        return identity if secret == expected else None

    return verify


ALICES_AGENT = DelegatedIdentity(
    agent_handle="alice-agent", agent_user_id=42, owner_handle="alice"
)


async def test_agent_token_resolves_to_its_own_handle_carrying_its_owner():
    actor = await resolve_actor(
        bearer_token="cxat_secret",
        verify_token=lambda _t: None,
        cheese_valid=_no_cheese,
        is_agent=_never_agent,
        cheese_handle="cheese",
        fallback_handle=None,
        verify_delegated=_delegated_verifier("cxat_secret", ALICES_AGENT),
    )
    assert actor is not None
    # Its own handle is what makes the agent's writes tellable apart from the
    # human's; owner_handle is what keeps its reach tied to hers.
    assert actor.handle == "alice-agent"
    assert actor.owner_handle == "alice"
    assert actor.user_id == 42
    assert actor.is_agent is True
    assert actor.via == "agent"
    assert actor.authenticated is True


async def test_a_human_session_token_is_never_shadowed_by_the_agent_path():
    """The human path runs first, so adding agent tokens cannot change who an
    existing caller resolves to."""
    actor = await resolve_actor(
        bearer_token="jwt-for-alice",
        verify_token=lambda _t: TokenIdentity(handle="alice", user_id=7),
        cheese_valid=_no_cheese,
        is_agent=_never_agent,
        cheese_handle="cheese",
        fallback_handle=None,
        verify_delegated=_delegated_verifier("jwt-for-alice", ALICES_AGENT),
    )
    assert actor is not None
    assert (actor.handle, actor.via, actor.owner_handle) == ("alice", "token", None)


async def test_an_unknown_secret_falls_through_untouched():
    actor = await resolve_actor(
        bearer_token="cxat_revoked",
        verify_token=lambda _t: None,
        cheese_valid=_no_cheese,
        is_agent=_never_agent,
        cheese_handle="cheese",
        fallback_handle="alice",
        verify_delegated=_delegated_verifier("cxat_live", ALICES_AGENT),
    )
    assert actor is not None
    assert actor.via == "handle"
    assert actor.authenticated is False


def _delegated_actor(owner: str = "alice") -> Actor:
    return Actor(
        handle=f"{owner}-agent",
        user_id=42,
        is_agent=True,
        via="agent",
        owner_handle=owner,
    )


async def _authorize(actor: Actor, *, members: set[str], roster: bool) -> bool:
    async def topic_role(_tid: uuid.UUID, handle: str):
        from app.domain.topic.models import TopicRole

        return TopicRole.member if handle in members else None

    async def roster_exists(_tid: uuid.UUID) -> bool:
        return roster

    async def is_project_member(_pid: uuid.UUID, handle: str) -> bool:
        return handle in members

    return await authorize_topic_access(
        actor,
        project_id=PROJECT,
        topic_id=TOPIC,
        topic_role=topic_role,
        roster_exists=roster_exists,
        is_project_member=is_project_member,
    )


async def test_an_agent_reaches_what_its_owner_reaches():
    assert await _authorize(_delegated_actor(), members={"alice"}, roster=True) is True


async def test_an_agent_is_refused_where_its_owner_would_be():
    """The escalation this design exists to prevent: a long-lived token that
    points at no resource must not inherit the per-turn agent pass, or running an
    agent would open every topic on the platform."""
    assert await _authorize(_delegated_actor(), members={"bob"}, roster=True) is False


async def test_being_on_the_roster_itself_does_not_widen_an_agents_reach():
    """Only the owner's membership counts. A seat handed to the agent alone would
    be a way to grant its runner access its account never had."""
    assert (
        await _authorize(_delegated_actor(), members={"alice-agent"}, roster=True)
        is False
    )


async def test_the_platform_agents_pass_through_is_untouched():
    """芝士 arrives with a token already scoped to this project/topic at the gate,
    so it keeps the pass it has always had."""
    cheese = Actor(handle="cheese", user_id=None, is_agent=True, via="cheese")
    assert await _authorize(cheese, members=set(), roster=True) is True
