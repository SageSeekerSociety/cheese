"""社交通知也走投递账本：同一件事重放一遍，收件人手里仍然一条（结论 58，I11）。

社交那几条通知（入队申请、邀请、审批结果、讨论里被 @）以前绕开账本：收件人由每个
调用点自己算成一组用户 id，直接交给渠道，既不记账也不去重。后果有两个，都在生产
里无声发生：

- **同一件事重放一遍就是第二条通知。** 一次审批被重投（客户端重试、补发、同一条
  事件被算两遍）在旧路径上落两行，因为那条路上没有任何东西记着「这件事已经通知过
  他了」。
- **没送出去的通知没有人记着。** 渠道那边收不下，异常被吞掉，没有一行记着它本该
  发出去，于是也没有人能再把它发出去。

所以这里验的是账本的两句话在社交这一侧也成立：去重键跟着**事件**走（事件 = 哪条
申请上发生了哪件事），以及每一条社交通知在发出去之前先在账本上留下一行。
"""

import pathlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.domain.delivery.models import Delivery
from app.domain.notification.models import Notification, NotificationType
from app.domain.team.membership_services import TeamMembershipService
from app.domain.team.models import (
    ApplicationStatus,
    ApplicationType,
    Team,
    TeamMemberRole,
    TeamMembershipApplication,
    TeamUserRelation,
)
from app.domain.team.repositories import (
    TeamMembershipApplicationRepository,
    TeamRepository,
)
from app.domain.user.models import User

pytestmark = pytest.mark.anyio

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


async def _user(session, handle: str) -> int:
    now = datetime.now(UTC)
    user = User(
        username=handle,
        email=f"{handle}@example.invalid",
        hashed_password="x",
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.flush()
    return user.id


async def _team(session, *, owner_id: int) -> int:
    now = datetime.now(UTC)
    team = Team(
        name=f"team-{uuid.uuid4().hex[:8]}",
        intro="intro",
        description="description",
        avatar_id=1,
        created_at=now,
        updated_at=now,
    )
    session.add(team)
    await session.flush()
    session.add(
        TeamUserRelation(
            team_id=team.id,
            user_id=owner_id,
            role=TeamMemberRole.OWNER,
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()
    return team.id


async def _pending_request(session, *, team_id: int, user_id: int) -> int:
    now = datetime.now(UTC)
    application = TeamMembershipApplication(
        user_id=user_id,
        team_id=team_id,
        initiator_id=user_id,
        type=ApplicationType.REQUEST.value,
        status=ApplicationStatus.PENDING.value,
        role="MEMBER",
        message="",
        created_at=now,
        updated_at=now,
    )
    session.add(application)
    await session.flush()
    return application.id


def _service(session) -> TeamMembershipService:
    return TeamMembershipService(
        session,
        TeamRepository(session),
        TeamMembershipApplicationRepository(session),
    )


async def _inbox(session, receiver_id: int) -> list[Notification]:
    rows = await session.scalars(
        select(Notification)
        .where(Notification.receiver_id == receiver_id)
        .order_by(Notification.id)
    )
    return list(rows)


async def _ledger_rows(session) -> list[Delivery]:
    rows = await session.scalars(select(Delivery).order_by(Delivery.recorded_at))
    return list(rows)


async def test_the_same_approval_reaches_the_requester_once(db_factory):
    """同一次成员申请审两遍 —— 申请人手里仍然一条。

    第二遍不是第二次点击：状态守卫挡得住那个。挡不住的是同一次审批被重投 —— 客户
    端重试、账本补发、同一条事件被算两遍，那一档在旧路径上每一次都落一条新通知。
    所以这里把申请放回它被审之前的样子，让同一次审批真的再走一遍。
    """
    async with db_factory() as session:
        owner = await _user(session, "owner")
        requester = await _user(session, "requester")
        team_id = await _team(session, owner_id=owner)
        request_id = await _pending_request(session, team_id=team_id, user_id=requester)

        await _service(session).approve_team_join_request(
            approver_user_id=owner, team_id=team_id, request_id=request_id
        )

        # 把这次审批放回它发生之前：申请还挂着，人还没进来。
        application = await session.get(TeamMembershipApplication, request_id)
        application.status = ApplicationStatus.PENDING.value
        repo = TeamRepository(session)
        await repo.soft_delete_member(
            await repo.get_member_relation(team_id, requester)
        )

        await _service(session).approve_team_join_request(
            approver_user_id=owner, team_id=team_id, request_id=request_id
        )
        await session.commit()

        inbox = await _inbox(session, requester)
        assert len(inbox) == 1, "同一次审批重投一遍，申请人收到了第二条"
        assert inbox[0].type == NotificationType.TEAM_REQUEST_APPROVED


async def test_canceling_an_invitation_is_a_second_event_on_the_same_record(db_factory):
    """一条邀请先发出再取消 —— 被邀请的人两条都收到。

    这两件事挂在同一条 `team_membership_application` 上，通知的还是同一个人，所以
    事件身份只按记录算是不够的：算进「这条记录上发生了哪件事」，取消那一条才有自
    己的去重键。只按记录算的话，`TEAM_INVITATION_CANCELED` 撞上 `TEAM_INVITATION`
    已经落下的那一行，账本当它是同一件事的重放，被邀请的人永远等不到取消的消息，
    而「同一件事只通知一次」那几条用例照样全绿 —— 它们每条都只在一条记录上发生一
    件事。
    """
    async with db_factory() as session:
        owner = await _user(session, "owner")
        invited = await _user(session, "invited")
        team_id = await _team(session, owner_id=owner)

        invitation = await _service(session).create_team_invitation(
            initiator_user_id=owner,
            team_id=team_id,
            user_id_to_invite=invited,
            role=None,
            message=None,
        )
        await _service(session).cancel_team_invitation(
            canceler_user_id=owner, team_id=team_id, invitation_id=invitation.id
        )
        await session.commit()

        assert [row.type for row in await _inbox(session, invited)] == [
            NotificationType.TEAM_INVITATION,
            NotificationType.TEAM_INVITATION_CANCELED,
        ]
        rows = await _ledger_rows(session)
        assert len(rows) == 2
        assert len({row.event_id for row in rows}) == 2, (
            "同一条邀请上的两件事共用了一个事件身份，取消那一条发不出去"
        )


async def test_a_join_request_is_recorded_before_it_is_sent(db_factory):
    """入队申请：每个管理员在账本上各有一行，发出去之后回写 `sent_at`。

    账本那一行是这条通知在「发出去」之前仅剩的记录 —— 渠道收不下的时候，靠它补
    发。收件人是 handle，不是用户 id（I11）：调用点把自己算出来的那组 id 翻成名册
    上的名字，投递这一侧只认名字。
    """
    async with db_factory() as session:
        owner = await _user(session, "owner")
        admin = await _user(session, "admin")
        requester = await _user(session, "requester")
        team_id = await _team(session, owner_id=owner)
        now = datetime.now(UTC)
        session.add(
            TeamUserRelation(
                team_id=team_id,
                user_id=admin,
                role=TeamMemberRole.ADMIN,
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()

        await _service(session).join(
            user_id=requester, team=await session.get(Team, team_id), message="让我进来"
        )
        await session.commit()

        rows = await _ledger_rows(session)
        assert {row.recipient_handle for row in rows} == {"owner", "admin"}
        assert all(row.sent_at is not None for row in rows)
        assert len({row.event_id for row in rows}) == 1, "同一条申请只有一个事件身份"
        assert len(await _inbox(session, owner)) == 1
        assert len(await _inbox(session, admin)) == 1
        assert await _inbox(session, requester) == []


async def test_a_request_to_a_team_with_no_admins_notifies_nobody(db_factory):
    """没有管理员可审的团队：谁也不通知，账本上也不留行。

    「谁也没点到」是常态，不是异常 —— 寻址给出空名单的时候，投递这一侧什么都不
    做，所以调用点不必自己先判一次「有没有人要通知」。
    """
    async with db_factory() as session:
        requester = await _user(session, "requester")
        now = datetime.now(UTC)
        team = Team(
            name=f"team-{uuid.uuid4().hex[:8]}",
            intro="intro",
            description="description",
            avatar_id=1,
            created_at=now,
            updated_at=now,
        )
        session.add(team)
        await session.flush()

        await _service(session).join(user_id=requester, team=team, message=None)
        await session.commit()

        assert await _ledger_rows(session) == []
        assert await _inbox(session, requester) == []


def test_no_call_site_can_send_a_notification_off_the_ledger():
    """守卫：全仓没有第二条发通知的路（I11 从目标变成现状）。

    扫源码而不是跑用例：这里要证的是「不存在这样一条路」。跑用例只能证被跑到的那
    几条路走了账本，而漏掉的恰恰是没人想起来去跑的那一处 —— 社交这 8 处当初就是
    这么留在账本外面的。
    """
    offenders = [
        f"{path}:{i}"
        for path in sorted(APP.rglob("*.py"))
        for i, line in enumerate(path.read_text().split("\n"), 1)
        if "publish_notification_event" in line or "NotificationTriggerEvent" in line
    ]
    assert offenders == [], f"又有调用点绕开账本发通知：{offenders}"
