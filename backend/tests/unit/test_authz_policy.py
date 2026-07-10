"""Composable authorization policy (pure, adapter-injected)."""

import uuid

import pytest

from app.domain.authz.policy import authorize_topic_access, can_manage_roster
from app.domain.identity.actor import Actor
from app.domain.topic.models import TopicRole

pytestmark = pytest.mark.anyio

PID = uuid.uuid4()
TID = uuid.uuid4()


def _actor(via: str, handle: str = "u", is_agent: bool = False) -> Actor:
    return Actor(handle=handle, user_id=None, is_agent=is_agent, via=via)


def _adapters(*, role=None, roster=True, project_member=False):
    async def topic_role(_tid, _handle):
        return role

    async def roster_exists(_tid):
        return roster

    async def is_project_member(_pid, _handle):
        return project_member

    return dict(
        topic_role=topic_role,
        roster_exists=roster_exists,
        is_project_member=is_project_member,
    )


async def _access(actor, **adapters):
    return await authorize_topic_access(
        actor, project_id=PID, topic_id=TID, **_adapters(**adapters)
    )


async def test_handle_fallback_is_permissive():
    # Pre-token callers keep working even if they're not members.
    assert await _access(_actor("handle"), role=None, project_member=False) is True


async def test_agent_always_allowed():
    assert await _access(_actor("cheese", is_agent=True), project_member=False) is True


async def test_token_member_of_topic_allowed():
    assert await _access(_actor("token"), role=TopicRole.member) is True


async def test_token_project_member_allowed():
    assert await _access(_actor("token"), role=None, project_member=True) is True


async def test_token_outsider_on_rostered_topic_denied():
    assert (
        await _access(_actor("token"), role=None, roster=True, project_member=False)
        is False
    )


async def test_token_on_rosterless_legacy_topic_allowed():
    assert (
        await _access(_actor("token"), role=None, roster=False, project_member=False)
        is True
    )


async def test_can_manage_roster_owner_admin_only():
    async def role_owner(_t, _h):
        return TopicRole.owner

    async def role_member(_t, _h):
        return TopicRole.member

    assert await can_manage_roster(
        _actor("token"), topic_id=TID, topic_role=role_owner
    ) is True
    assert await can_manage_roster(
        _actor("token"), topic_id=TID, topic_role=role_member
    ) is False
    # Fallback path defers to the service's own role check → permissive here.
    assert await can_manage_roster(
        _actor("handle"), topic_id=TID, topic_role=role_member
    ) is True
