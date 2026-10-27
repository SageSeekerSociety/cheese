"""A person's 芝士 conversation has a computer of its own: a standard sandbox
in no project, which only that person's 芝士 may ask for, paid for from their
own credits, and destroyed when idle like any other — with nothing of it
coming back.

The cloud is the one ``test_sandbox_idle_stop`` builds: hosts are directories
on this machine and a fake MicroCloud.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from sqlalchemy import delete, select, update

from app.core.config import settings
from app.core.sandbox_auth import (
    mint_personal_credential,
    mint_personal_execution_credential,
)
from app.domain.assistant.models import AssistantConversation
from app.domain.conversation.models import Conversation, ConversationKind
from app.domain.machine import metering
from app.domain.machine.runner import ComputeMeterSweeper
from app.domain.machine.sandbox_wait import SANDBOX_REPLACED
from app.domain.team.services import team_service
from app.domain.usage.ledger import Ledger, payer_for_person
from app.domain.usage.models import ComputeRun, ResourceUsage
from app.domain.user.services import user_by_handle
from tests.conftest import seed_user
from tests.integration.test_a_checkpoint_after_its_sandbox_is_gone import (
    _a_cloud_machine,
)
from tests.integration.test_sandbox_idle_stop import cloud as cloud
from tests.integration.test_sandbox_idle_stop import (
    home_of,
    host_comes_up,
    run,
    sweep,
    time_passes,
    tool_call,
)

PRICE = 12.0  # credits per sandbox-hour, as the suite sets it


@pytest.fixture(autouse=True)
def _a_session_host(monkeypatch):
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    # Whether a payer still has credits is asked once a minute per process.
    monkeypatch.setattr(metering, "_checked_at", None)


def conversation_of(case, handle: str) -> uuid.UUID:
    seed_user(case.client, handle)

    async def make():
        async with case.client.test_request_factory() as db:
            user = await user_by_handle(db, handle)
            row = AssistantConversation(
                user_id=user.id, place_kind="none", place_id="", title=""
            )
            db.add(row)
            await db.commit()
            return row.id

    return run(case, make)


def seat_of(case, handle: str, conversation: uuid.UUID, *, session=None):
    """What the conversation's session asks with: ``handle``'s own credential
    for it."""

    async def user_id():
        async with case.client.test_request_factory() as db:
            return (await user_by_handle(db, handle)).id

    token = mint_personal_credential(
        user_id=run(case, user_id), conversation_id=str(conversation)
    )
    return SimpleNamespace(
        room=conversation, session=session or conversation, token=token
    )


def registered(case, conversation: uuid.UUID) -> Conversation | None:
    async def read():
        async with case.client.test_request_factory() as db:
            return await db.get(Conversation, conversation)

    return run(case, read)


@pytest.fixture
def mine(cloud):
    conversation = conversation_of(cloud, "alice")
    return SimpleNamespace(
        conversation=conversation, seat=seat_of(cloud, "alice", conversation)
    )


def test_a_persons_conversation_is_one_in_no_project(cloud, mine):
    row = registered(cloud, mine.conversation)
    assert (row.kind, row.project_id) == (ConversationKind.personal, None)

    async def drop():
        async with cloud.client.test_request_factory() as db:
            await db.execute(
                delete(AssistantConversation).where(
                    AssistantConversation.id == mine.conversation
                )
            )
            await db.commit()

    run(cloud, drop)
    assert registered(cloud, mine.conversation) is None


def test_the_conversation_gets_a_sandbox_of_its_own(cloud, mine):
    working(cloud, mine)
    home = home_of(cloud, mine.seat)
    assert (home.project_id, home.topic_id) == (None, mine.conversation)


def _refused(case, seat) -> int:
    return case.client.post(
        f"/topics/{seat.room}/sessions/{seat.session}/work-lease",
        headers={"Authorization": f"Bearer {seat.token}"},
        json={"env": {}, "timeout": 5},
    ).status_code


def test_nobody_else_may_ask_for_it(cloud, mine):
    seed_user(cloud.client, "bob")
    # Someone else's credential, naming this conversation.
    assert _refused(cloud, seat_of(cloud, "bob", mine.conversation)) == 401
    # The person's own, for a session the conversation does not have.
    assert (
        _refused(
            cloud, seat_of(cloud, "alice", mine.conversation, session=uuid.uuid4())
        )
        == 401
    )
    # A room's session, asking at the conversation.
    room = cloud.seats[0]
    stray = SimpleNamespace(
        room=mine.conversation, session=room.session, token=room.token
    )
    assert _refused(cloud, stray) == 404
    assert home_of(cloud, mine.seat) is None


def personal_team(case) -> int:
    async def ensure():
        async with case.client.test_request_factory() as db:
            alice = await user_by_handle(db, "alice")
            team = await team_service(db).ensure_personal_team(alice.id)
            await db.commit()
            return team.id

    return run(case, ensure)


def working(case, mine) -> dict:
    """The conversation's session gets its sandbox, on a new host; returns
    what it was answered."""
    assert tool_call(case, mine.seat).get("preparing")
    host_comes_up(case, mine.seat, "host-a")
    answer = tool_call(case, mine.seat)
    assert answer["target"]["device_id"] == "host-a"
    return answer


def test_its_time_is_paid_from_the_persons_own_credits(cloud, mine):
    personal = personal_team(cloud)
    working(cloud, mine)

    def meter():
        return run(cloud, ComputeMeterSweeper(cloud.client.test_request_factory).sweep)

    assert meter()["opened"] == 1

    async def thirty_minutes_ago():
        async with cloud.client.test_request_factory() as db:
            then = datetime.now(UTC) - timedelta(minutes=30)
            await db.execute(
                update(ComputeRun)
                .where(ComputeRun.topic_id == mine.conversation)
                .values(started_at=then, billed_until=then)
            )
            await db.commit()

    run(cloud, thirty_minutes_ago)
    assert meter()["charged"] == 1

    async def charges():
        async with cloud.client.test_request_factory() as db:
            return list(
                await db.scalars(
                    select(ResourceUsage).where(
                        ResourceUsage.conversation_id == mine.conversation,
                        ResourceUsage.route == "compute",
                    )
                )
            )

    [charge] = run(cloud, charges)
    assert (charge.team_id, charge.project_id) == (personal, None)
    assert charge.credits == pytest.approx(PRICE / 2)


def test_once_idle_it_is_gone_and_the_next_one_says_nothing_came_back(cloud, mine):
    working(cloud, mine)

    time_passes(cloud, mine.seat, timedelta(minutes=11))
    assert sweep(cloud)["destroyed"] == 1
    assert home_of(cloud, mine.seat) is None

    answer = tool_call(cloud, mine.seat, tells_agent=True)
    assert answer["target"]["device_id"] == "host-a"
    assert answer["notice"] == SANDBOX_REPLACED


def test_with_the_persons_credits_spent_it_is_given_back_and_none_starts(cloud, mine):
    working(cloud, mine)

    async def spend():
        async with cloud.client.test_request_factory() as db:
            alice = await user_by_handle(db, "alice")
            await Ledger(db).charge(await payer_for_person(db, alice.id), 10_000)
            await db.commit()

    run(cloud, spend)
    assert sweep(cloud)["destroyed"] == 1
    assert home_of(cloud, mine.seat) is None
    assert "额度已用完" in tool_call(cloud, mine.seat)["unavailable"]
    assert home_of(cloud, mine.seat) is None
    # The project's rooms pay from elsewhere, and are not held up by it.
    assert "unavailable" not in tool_call(cloud, cloud.seats[0])


def test_only_its_own_session_works_in_it(cloud, mine):
    lease = working(cloud, mine)
    _a_cloud_machine(cloud, "host-a")
    # Where the lease sends the session's executor calls.
    url = urlsplit(lease["target"]["url"]).path

    def ping(token) -> int:
        return cloud.client.post(
            url, json={"method": "ping"}, headers={"Authorization": f"Bearer {token}"}
        ).status_code

    assert ping(lease["token"]) == 200

    async def bob():
        async with cloud.client.test_request_factory() as db:
            return (await user_by_handle(db, "bob")).id

    seed_user(cloud.client, "bob")
    forged = mint_personal_execution_credential(
        user_id=run(cloud, bob),
        conversation_id=str(mine.conversation),
        session_id=str(mine.conversation),
        lease_generation=lease["target"]["generation"],
    )
    assert ping(forged) == 404
    # Neither the credential it asks for its sandbox with, nor a room's.
    assert ping(mine.seat.token) == 401
    assert ping(cloud.seats[0].token) == 401
