"""Composable authorization policy (pure, adapter-injected)."""

import uuid

import pytest

from app.domain.authz.policy import (
    authorize_topic_access,
    can_manage_project_members,
    can_manage_roster,
    refuse_unauthenticated_chat,
)
from app.domain.identity.actor import Actor
from app.domain.project.models import ProjectRole
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


# ---- refuse_unauthenticated_chat: who gets onto a chat socket at all ----
# Not covered by the integration suite alone: the "presented but unverifiable"
# vs "presented nothing" split is the whole point of the function, and only a
# table like this shows that `allow_anonymous` moves exactly one of the four
# cells. The chat route maps a returned code straight onto the wire, so these
# strings are the frontend's contract, not an implementation detail.


def _refusal(*, authenticated: bool, token_presented: bool, allow_anonymous: bool):
    return refuse_unauthenticated_chat(
        _actor("token" if authenticated else "handle"),
        token_presented=token_presented,
        allow_anonymous=allow_anonymous,
    )


async def test_authenticated_socket_is_admitted():
    for allow in (False, True):
        assert (
            _refusal(authenticated=True, token_presented=True, allow_anonymous=allow)
            is None
        )


async def test_unverifiable_token_is_refused_even_for_a_harness():
    # The incident's shape: treating a rejected credential as "no credential".
    # `allow_anonymous` must not be able to switch that downgrade back on.
    for allow in (False, True):
        assert _refusal(
            authenticated=False, token_presented=True, allow_anonymous=allow
        ) == ("auth_expired", "登录状态已失效，请重新登录后再发言")


async def test_tokenless_socket_is_refused_unless_the_harness_switch_is_on():
    assert _refusal(
        authenticated=False, token_presented=False, allow_anonymous=False
    ) == ("auth_required", "请先登录再进入话题")
    assert (
        _refusal(authenticated=False, token_presented=False, allow_anonymous=True)
        is None
    )


async def test_can_manage_roster_owner_admin_only():
    async def role_owner(_t, _h):
        return TopicRole.owner

    async def role_member(_t, _h):
        return TopicRole.member

    assert (
        await can_manage_roster(_actor("token"), topic_id=TID, topic_role=role_owner)
        is True
    )
    assert (
        await can_manage_roster(_actor("token"), topic_id=TID, topic_role=role_member)
        is False
    )
    # Fallback path defers to the service's own role check → permissive here.
    assert (
        await can_manage_roster(_actor("handle"), topic_id=TID, topic_role=role_member)
        is True
    )


# --- Project roster (who may add/remove members, change project roles) -------
#
# The project roster is the floor of topic access control (a project member
# reaches every topic above), so unlike every other judgment here it refuses the
# Phase-0 handle fallback outright.

OWNER = "alice"


def _project_adapters(*, owner: str | None = OWNER, roles: dict | None = None):
    async def project_owner(_pid):
        return owner

    async def project_role(_pid, handle):
        return (roles or {}).get(handle)

    return dict(project_owner=project_owner, project_role=project_role)


async def _may_manage(actor, **adapters):
    return await can_manage_project_members(
        actor, project_id=PID, **_project_adapters(**adapters)
    )


async def test_project_owner_may_manage_members():
    assert await _may_manage(_actor("token", OWNER)) is True


async def test_project_lead_may_manage_members():
    assert await _may_manage(_actor("token", "bob"), roles={"bob": ProjectRole.lead})


async def test_project_lead_may_manage_when_owner_is_null():
    # owner_handle is nullable in practice; without leads counting, an owner-less
    # project's roster would be frozen with no way to recover.
    assert await _may_manage(
        _actor("token", "bob"), owner=None, roles={"bob": ProjectRole.lead}
    )


async def test_project_member_and_mentor_may_not_manage_members():
    assert not await _may_manage(
        _actor("token", "bob"), roles={"bob": ProjectRole.member}
    )
    assert not await _may_manage(
        _actor("token", "carol"), roles={"carol": ProjectRole.mentor}
    )


async def test_project_outsider_with_a_token_may_not_manage_members():
    assert await _may_manage(_actor("token", "mallory")) is False


async def test_claimed_handle_may_not_manage_members():
    # Claiming to be the owner proves nothing — a claim being enough WAS the hole.
    assert await _may_manage(_actor("handle", OWNER)) is False
    assert await _may_manage(_actor("handle", "anonymous")) is False


async def test_agent_may_not_manage_members():
    # 芝士 promoting a member to lead through this surface is what exposed the
    # missing check; a 分身 asks a human instead — even holding a lead role.
    assert await _may_manage(_actor("cheese", "cheese", is_agent=True)) is False
    assert not await _may_manage(
        _actor("token", "cheese", is_agent=True), roles={"cheese": ProjectRole.lead}
    )
