"""一条社交通知没发出去，账本里留着行，下一轮补发把它补出去（结论 58）。

这一档在生产里是无声的：渠道收不下，异常被日志吞掉，申请人什么也没收到，而管理员
那边显示申请已经递出去了。以前没有任何一行记着这条通知本该发出去，所以没有人能再
把它发出去 —— 社交这几条通知走上账本之后才有第二次机会。

走的是真的 HTTP 入口：要证的是「这条路上发出去的通知也记账」，而不是「账本被调用
的时候会记账」。
"""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.delivery import ledger as ledger_module
from app.domain.delivery.ledger import Ledger
from app.domain.delivery.models import Delivery
from app.domain.notification.models import Notification, NotificationType
from tests.integration.conftest import UserCreator, unique_int
from tests.support.failing_channels import channels_refuse


def test_a_join_request_no_channel_took_is_resent(
    user_client: UserCreator,
    api_client: TestClient,
    db_session,
    _portal,
    monkeypatch,
):
    owner = user_client.create_user()
    owner.token = user_client.login(api_client, owner.username, owner.password)
    requester = user_client.create_user()
    requester.token = user_client.login(
        api_client, requester.username, requester.password
    )

    team_resp = api_client.post(
        "/teams",
        json={
            "name": f"Ledger Test Team ({unique_int()})",
            "intro": "Team for testing the delivery ledger",
            "description": "A lengthy description. " * 10,
            "avatarId": 1,
        },
        headers={"Authorization": f"Bearer {owner.token}"},
    )
    assert team_resp.status_code == 201, team_resp.text
    team_id = team_resp.json()["data"]["team"]["id"]

    # 一个渠道都收不下 —— 站内信那一次写入在自己的 savepoint 里失败。
    monkeypatch.setattr(
        ledger_module, "build_notification_event_handler", channels_refuse
    )
    resp = api_client.post(
        f"/teams/{team_id}/join",
        json={"message": "让我进来"},
        headers={"Authorization": f"Bearer {requester.token}"},
    )
    assert resp.status_code == 200, resp.text
    monkeypatch.undo()

    async def owners_inbox() -> list[Notification]:
        rows = await db_session.scalars(
            select(Notification).where(Notification.receiver_id == owner.user_id)
        )
        return list(rows)

    async def unsent() -> list[Delivery]:
        rows = await db_session.scalars(
            select(Delivery).where(Delivery.sent_at.is_(None))
        )
        return list(rows)

    async def check_recorded_but_not_sent() -> None:
        assert await owners_inbox() == [], "渠道收不下，收件箱里不该有东西"
        (row,) = await unsent()
        assert row.recipient_handle == owner.username
        assert row.type == NotificationType.TEAM_JOIN_REQUEST.value

    _portal.call(check_recorded_but_not_sent)

    async def resend() -> None:
        assert await Ledger(db_session).resend_unsent() == 1
        landed = await owners_inbox()
        assert len(landed) == 1, "补发没把它补出来"
        assert landed[0].type == NotificationType.TEAM_JOIN_REQUEST
        assert await unsent() == []

    _portal.call(resend)
