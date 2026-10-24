"""Inviting a friend: whose code they came in on, and what each step pays.

The rules are the team's 《内测激励与裂变机制设计方案》:
a code admits one person until its owner has accepted a delivery, then three
more, and every friend who gets started gives their seat back; the friend is paid 50 for their first project and 100 for accepting a
delivery within seven days, which also pays the inviter 150 credits and records
50 contribution points (not shown to anyone yet); each friend pays out once.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.domain.invite.models import InviteReferral
from app.domain.usage.models import CreditReward
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)
from tests.integration.test_accept import remote_delivery as remote_delivery
from tests.integration.test_accept_does_not_archive import _accept, _card
from tests.integration.test_accept_pr import app_world as app_world
from tests.integration.test_account_uniqueness import _registration
from tests.integration.test_credit_plans import _auth


def _invites(client, headers) -> dict:
    response = client.get("/users/me/invites", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _credits(client, headers) -> float:
    response = client.get("/users/me/credits/usage", headers=headers)
    assert response.status_code == 200, response.text
    return sum(p["credits_total"] for p in response.json()["data"]["packs"])


def _days_left(client, headers) -> set[int]:
    """Whole days each pack has left before it expires."""
    response = client.get("/users/me/credits/usage", headers=headers)
    assert response.status_code == 200, response.text
    now = datetime.now(UTC)
    return {
        (datetime.fromisoformat(p["expires_at"]) - now).days
        for p in response.json()["data"]["packs"]
    }


def _sources(client, headers) -> set[str]:
    response = client.get("/users/me/credits/usage", headers=headers)
    assert response.status_code == 200, response.text
    return {p["source"] for p in response.json()["data"]["packs"]}


def _referrer_points(client, _portal) -> int:
    """Contribution points recorded for inviters. No page shows them; the
    record is kept, so it is read from the database."""

    async def read() -> int:
        async with client.test_factory() as session:
            rows = await session.scalars(
                select(CreditReward.points).where(
                    CreditReward.key.like("invite:referrer:%")
                )
            )
            return sum(rows)

    return _portal.call(read)


def _register(client, _portal, code: str | None) -> tuple[str, dict]:
    suffix = uuid.uuid4().hex[:8]
    body = _portal.call(
        _registration, f"friend_{suffix}", f"friend-{suffix}@example.com"
    )
    if code is not None:
        body["inviteCode"] = code
    response = client.post("/users", json=body)
    assert response.status_code == 200, response.text
    token = response.json()["data"]["accessToken"]
    return body["username"], {"Authorization": f"Bearer {token}"}


def _onboard(client, headers) -> str:
    response = client.post(
        "/users/me/onboarding",
        headers=headers,
        json={"project_id": str(uuid.uuid4()), "project_name": "第一个项目"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["project_id"]


def _accept_a_delivery(client, handle: str) -> None:
    """``handle`` accepts a delivery in a project of their own."""
    response = post_project(client, json={"name": "交付"}, owner=handle)
    assert response.status_code == 200, response.text
    project_id = response.json()["data"]["id"]
    join_project_team(client, project_id, handle)
    room = client.post(
        "/topics",
        json={"project_id": project_id, "title": "做一个东西"},
        headers=session_auth_headers(handle),
    )
    assert room.status_code == 200, room.text
    card = _card(client, room.json()["data"]["id"], reviewer=handle)
    assert card.status_code == 200, card.text
    accepted = _accept(client, card.json()["data"]["id"], by=handle)
    assert accepted.status_code == 200, accepted.text


@pytest.fixture
def inviter(client) -> tuple[str, dict]:
    return "inviter", _auth(client, "inviter")


def test_a_friend_who_registers_with_my_code_is_mine(client, _portal, inviter):
    _, mine = inviter
    before = _invites(client, mine)
    assert (before["max_uses"], before["use_count"], before["unlocked"]) == (
        1,
        0,
        False,
    )
    assert before["invitees"] == []

    handle, _ = _register(client, _portal, before["code"])

    after = _invites(client, mine)
    assert after["code"] == before["code"]
    assert after["use_count"] == 1
    assert [(p["handle"], p["stage"]) for p in after["invitees"]] == [
        (handle, "registered")
    ]


def test_open_registration_refuses_an_exhausted_code_when_supplied(
    client, _portal, inviter
):
    _, mine = inviter
    code = _invites(client, mine)["code"]
    _register(client, _portal, code)

    suffix = uuid.uuid4().hex[:8]
    body = _portal.call(_registration, f"late_{suffix}", f"late-{suffix}@example.com")
    body["inviteCode"] = code
    assert client.post("/users", json=body).status_code == 422
    assert _invites(client, mine)["use_count"] == 1


def test_open_registration_refuses_to_mail_a_code_on_a_used_up_link(
    client, _portal, inviter
):
    """A friend who explicitly submitted an exhausted code learns right away
    rather than after verifying their email."""
    _, mine = inviter
    code = _invites(client, mine)["code"]
    _register(client, _portal, code)

    response = client.post(
        "/users/verify/email",
        json={"email": f"late-{uuid.uuid4().hex[:8]}@example.com", "inviteCode": code},
    )
    assert response.status_code == 422


def test_invite_only_registration_refuses_a_used_up_code(
    client, _portal, inviter, monkeypatch
):
    monkeypatch.setattr(settings, "require_invite_code", True)
    _, mine = inviter
    code = _invites(client, mine)["code"]
    _register(client, _portal, code)

    suffix = uuid.uuid4().hex[:8]
    body = _portal.call(_registration, f"late_{suffix}", f"late-{suffix}@example.com")
    body["inviteCode"] = code
    assert client.post("/users", json=body).status_code != 200


def test_an_administrators_code_invites_nobodys_friend(client, _portal, monkeypatch):
    monkeypatch.setattr(settings, "platform_admin_handles", ["code-admin"])
    admin = _auth(client, "code-admin")
    made = client.post("/users/invite-codes", headers=admin, json={"maxUses": 5})
    assert made.status_code == 200, made.text

    _, headers = _register(client, _portal, made.json()["data"]["code"])
    _onboard(client, headers)

    assert _credits(client, headers) == 0
    assert _invites(client, admin)["invitees"] == []


def test_the_friends_first_project_pays_them_fifty_once(client, _portal, inviter):
    _, mine = inviter
    _, theirs = _register(client, _portal, _invites(client, mine)["code"])

    _onboard(client, theirs)
    _onboard(client, theirs)

    assert _credits(client, theirs) == 50
    assert _sources(client, theirs) == {"reward"}
    assert _credits(client, mine) == 0
    assert _invites(client, mine)["invitees"][0]["stage"] == "onboarded"


def test_creating_a_project_directly_without_the_guide_still_pays_fifty(
    client, _portal, inviter
):
    _, mine = inviter
    friend, theirs = _register(client, _portal, _invites(client, mine)["code"])

    res = post_project(client, json={"name": "直接建项"}, owner=friend)
    assert res.status_code == 200, res.text

    assert _credits(client, theirs) == 50
    assert _invites(client, mine)["invitees"][0]["stage"] == "onboarded"


def test_an_external_github_merge_still_unlocks_seats_and_pays_referral(
    client, _portal, inviter
):
    from app.domain.delivery.ledger import settle_card
    from app.domain.review.models import AcceptCard
    from app.domain.review.queries import has_accepted
    from app.domain.review.services import AcceptService
    from app.domain.topic.models import Topic

    _, mine = inviter
    friend, theirs = _register(client, _portal, _invites(client, mine)["code"])
    res = post_project(client, json={"name": "GitHub合并"}, owner=friend)
    project_id = res.json()["data"]["id"]
    join_project_team(client, project_id, friend)
    room = client.post(
        "/topics",
        json={"project_id": project_id, "title": "外部PR"},
        headers=session_auth_headers(friend),
    ).json()["data"]["id"]
    card_id = uuid.UUID(_card(client, room, reviewer=friend).json()["data"]["id"])

    async def _external_merge() -> bool:
        async with client.test_factory() as session:
            card = await session.get(AcceptCard, card_id)
            topic = await session.get(Topic, uuid.UUID(room))
            assert card is not None and topic is not None
            card.pr_number = 42
            card.pr_url = "https://github.com/example/repo/pull/42"
            card.pr_merged_at = datetime.now(UTC)
            card.decided_by = None

            async def _noop(*_a, **_kw) -> None:
                return None

            svc = AcceptService(session, announce=_noop, settle_card=settle_card)
            await svc._finish_pr_accept(card=card, topic=topic, merged_externally=True)
            await session.commit()
            return await has_accepted(session, friend)

    assert _portal.call(_external_merge) is True
    assert _credits(client, theirs) == 150
    assert _credits(client, mine) == 150
    assert _invites(client, theirs)["max_uses"] == 4


def test_someone_who_came_in_without_a_code_is_paid_nothing_for_onboarding(
    client,
    _portal,
):
    _, headers = _register(client, _portal, None)
    _onboard(client, headers)
    assert _credits(client, headers) == 0


def test_the_friends_first_acceptance_pays_both_sides_once(client, _portal, inviter):
    _, mine = inviter
    friend, theirs = _register(client, _portal, _invites(client, mine)["code"])
    _onboard(client, theirs)

    _accept_a_delivery(client, friend)
    _accept_a_delivery(client, friend)

    assert _credits(client, theirs) == 150
    assert _credits(client, mine) == 150
    seen = _invites(client, mine)
    assert seen["invite_credits"] == 150
    assert (seen["month_credits"], seen["month_cap"]) == (150, 1500)
    assert _referrer_points(client, _portal) == 50
    assert seen["invitees"][0]["stage"] == "activated"
    # Invitation packs last a year, like every other reward.
    assert _days_left(client, theirs) == _days_left(client, mine) == {364}


def test_an_acceptance_after_seven_days_pays_nobody(client, _portal, inviter):
    _, mine = inviter
    friend, theirs = _register(client, _portal, _invites(client, mine)["code"])

    async def _eight_days_ago() -> None:
        async with client.test_factory() as session:
            await session.execute(
                update(InviteReferral).values(
                    registered_at=datetime.now(UTC) - timedelta(days=8)
                )
            )
            await session.commit()

    _portal.call(_eight_days_ago)

    _accept_a_delivery(client, friend)

    # First-project 50 has no 7-day limit; the 100/150 acceptance reward does.
    assert _credits(client, theirs) == 50
    assert _credits(client, mine) == 0
    assert _invites(client, mine)["invitees"][0]["stage"] == "lapsed"


def test_my_own_first_acceptance_adds_three_seats(client, inviter):
    handle, mine = inviter
    assert _invites(client, mine)["max_uses"] == 1
    _accept_a_delivery(client, handle)
    _accept_a_delivery(client, handle)

    seen = _invites(client, mine)
    assert (seen["max_uses"], seen["unlocked"]) == (4, True)


def test_a_friend_who_gets_started_gives_their_seat_back_once(client, _portal, inviter):
    _, mine = inviter
    friend, _ = _register(client, _portal, _invites(client, mine)["code"])
    used_up = _invites(client, mine)
    assert used_up["max_uses"] - used_up["use_count"] == 0

    _accept_a_delivery(client, friend)
    _accept_a_delivery(client, friend)

    seen = _invites(client, mine)
    assert (seen["max_uses"], seen["use_count"]) == (2, 1)


def test_a_friend_who_starts_too_late_gives_no_seat_back(client, _portal, inviter):
    _, mine = inviter
    friend, _ = _register(client, _portal, _invites(client, mine)["code"])

    async def _eight_days_ago() -> None:
        async with client.test_factory() as session:
            await session.execute(
                update(InviteReferral).values(
                    registered_at=datetime.now(UTC) - timedelta(days=8)
                )
            )
            await session.commit()

    _portal.call(_eight_days_ago)
    _accept_a_delivery(client, friend)

    assert _invites(client, mine)["max_uses"] == 1


def test_someone_who_accepted_before_ever_opening_the_page_starts_unlocked(
    client,
    _portal,
):
    handle, headers = _register(client, _portal, None)
    _accept_a_delivery(client, handle)

    assert _invites(client, headers)["max_uses"] == 4


def test_past_the_monthly_cap_the_inviter_gets_no_credits_but_points_are_kept(
    client, _portal, inviter, monkeypatch
):
    from app.domain.usage import referrals

    monkeypatch.setattr(referrals, "INVITER_MONTHLY_CAP", 150)
    handle, mine = inviter
    _accept_a_delivery(client, handle)  # four seats
    code = _invites(client, mine)["code"]
    for _ in range(2):
        friend, _ = _register(client, _portal, code)
        _accept_a_delivery(client, friend)

    seen = _invites(client, mine)
    assert seen["invite_credits"] == 150
    assert seen["month_credits"] == 150
    assert _referrer_points(client, _portal) == 100
    assert sorted(p["credits"] for p in seen["invitees"]) == [0, 150]
    assert _credits(client, mine) == 150
    # The cap limits credits, not friends: the unpaid one gave their seat back too.
    assert seen["max_uses"] == 4 + 2


def test_the_monthly_cap_starts_again_next_month(client, _portal, inviter, monkeypatch):
    from app.domain.usage import referrals

    monkeypatch.setattr(referrals, "INVITER_MONTHLY_CAP", 150)
    handle, mine = inviter
    _accept_a_delivery(client, handle)
    code = _invites(client, mine)["code"]
    first, _ = _register(client, _portal, code)
    _accept_a_delivery(client, first)

    async def _paid_last_month() -> None:
        async with client.test_factory() as session:
            await session.execute(
                update(CreditReward)
                .where(CreditReward.key.like("invite:referrer:%"))
                .values(created_at=datetime.now(UTC) - timedelta(days=40))
            )
            await session.commit()

    _portal.call(_paid_last_month)
    second, _ = _register(client, _portal, code)
    _accept_a_delivery(client, second)

    assert _credits(client, mine) == 300
    assert _invites(client, mine)["month_credits"] == 150


def test_two_first_views_at_once_make_one_code(client, _portal, inviter):
    """The public shared page can be a person's first view of their own code,
    opened by several readers at the same moment."""
    from app.domain.usage import referrals
    from app.domain.user.models import User

    async def never() -> bool:
        return False

    async def _race() -> tuple[str, str]:
        async with client.test_factory() as a, client.test_factory() as b:
            user_a = await a.scalar(select(User).where(User.username == "inviter"))
            user_b = await b.scalar(select(User).where(User.username == "inviter"))
            first = await referrals.personal_code(a, user_a, has_accepted=never)
            # b reads before a commits, then waits on a's row.
            second = asyncio.create_task(
                referrals.personal_code(b, user_b, has_accepted=never)
            )
            await asyncio.sleep(0.3)
            await a.commit()
            return first.code, (await second).code

    first, second = _portal.call(_race)
    assert first == second


def test_a_shared_page_offers_the_sharers_seats_while_they_last(
    client, _portal, inviter
):
    _, mine = inviter
    project = post_project(client, json={"name": "D"}, owner="inviter")
    doc = client.post(
        f"/projects/{project.json()['data']['id']}/documents",
        headers=mine,
        json={"title": "讲义", "content": "正文"},
    )
    assert doc.status_code == 200, doc.text
    document_id = doc.json()["data"]["id"]
    share = client.put(f"/documents/{document_id}/share", headers=mine)
    assert share.status_code == 200, share.text
    token = share.json()["data"]["link"]["token"]

    shared_by = client.get(f"/shared/{token}").json()["data"]["shared_by"]
    assert shared_by["handle"] == "inviter"
    assert (shared_by["code"], shared_by["seats_left"]) == (
        _invites(client, mine)["code"],
        1,
    )

    _register(client, _portal, shared_by["code"])
    after = client.get(f"/shared/{token}").json()["data"]["shared_by"]
    assert (after["code"], after["seats_left"]) == (None, 0)
