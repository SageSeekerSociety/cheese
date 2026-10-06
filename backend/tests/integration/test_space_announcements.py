"""空间公告：一条一行，谁能发、发给谁、改和删之后动态里剩下什么。

规则都是在代码之前就能说出来的：

- 只有空间的所有者与管理员能发公告，成员只能读；
- 发布时，空间里除发布人以外的每个人在自己的动态里收到恰好一条，未读；
  发布人自己一条也没有；
- 公告默认站内 + 邮件（折进摘要）、不推送，收件人可自行关掉；
- 改公告不再通知任何人；
- 删公告，它发出去的通知一起消失；
- 过了到期时间的公告不在「当前」里（题目列表顶上那一栏读的就是它），
  而在「已到期」里；
- 两位管理员各改各的公告，谁也不会把对方的改动覆盖掉。
"""

import time

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.delivery.models import ChannelDelivery
from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    unique_int,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(user_client: UserCreator, api_client: TestClient, user) -> str:
    return user_client.login(api_client, user.username, user.password)


def _space_with_members(
    user_client: UserCreator, api_client: TestClient, members: int = 2
) -> tuple[int, str, list[str]]:
    """A reviewed space: its creator's token and its members' tokens."""
    creator = user_client.create_user()
    creator_token = _login(user_client, api_client, creator)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Announcement Space ({unique_int(10000000, 99999999)})",
            "intro": "",
            "description": "",
            "avatarId": 1,
            "enableRank": False,
            "taskTemplates": [],
        },
        headers=_auth(creator_token),
    )
    assert resp.status_code == 201, resp.text
    space_id = resp.json()["data"]["space"]["id"]
    tokens = []
    for _ in range(members):
        member = user_client.create_user()
        added = api_client.post(
            f"/spaces/{space_id}/members",
            json={"userId": member.user_id},
            headers=_auth(creator_token),
        )
        assert added.status_code == 201, added.text
        tokens.append(_login(user_client, api_client, member))
    return space_id, creator_token, tokens


def _publish(api_client: TestClient, token: str, space_id: int, **body) -> dict:
    resp = api_client.post(
        f"/spaces/{space_id}/announcements",
        json={"title": "期中报告改为统一提交 PDF", "content": "<p>截止时间不变</p>"}
        | body,
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["announcement"]


def _listed(api_client: TestClient, token: str, space_id: int) -> dict:
    resp = api_client.get(f"/spaces/{space_id}/announcements", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _feed(api_client: TestClient, token: str) -> list[dict]:
    resp = api_client.get(
        "/notifications",
        params={"type": "SPACE_ANNOUNCEMENT", "pageSize": 50},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["notifications"]


class TestWhoMayPublish:
    def test_a_member_cannot_publish_edit_or_delete(
        self, user_client: UserCreator, api_client: TestClient
    ):
        space_id, owner, (member,) = _space_with_members(user_client, api_client, 1)
        refused = api_client.post(
            f"/spaces/{space_id}/announcements",
            json={"title": "成员发的公告"},
            headers=_auth(member),
        )
        assert refused.status_code == 403, refused.text

        published = _publish(api_client, owner, space_id)
        path = f"/spaces/{space_id}/announcements/{published['id']}"
        assert (
            api_client.patch(path, json={"title": "改掉"}, headers=_auth(member))
        ).status_code == 403
        assert api_client.delete(path, headers=_auth(member)).status_code == 403

        # The member still reads it, unchanged.
        (only,) = _listed(api_client, member, space_id)["current"]
        assert only["title"] == published["title"]

    def test_someone_outside_the_space_cannot_read_it(
        self, user_client: UserCreator, api_client: TestClient
    ):
        space_id, owner, _ = _space_with_members(user_client, api_client, 0)
        _publish(api_client, owner, space_id)
        outsider = user_client.create_user()
        token = _login(user_client, api_client, outsider)
        resp = api_client.get(f"/spaces/{space_id}/announcements", headers=_auth(token))
        assert resp.status_code == 404, resp.text


class TestPublishingNotifies:
    def test_each_member_gets_one_unread_notice_and_the_author_none(
        self, user_client: UserCreator, api_client: TestClient
    ):
        space_id, owner, members = _space_with_members(user_client, api_client, 2)
        published = _publish(api_client, owner, space_id)

        for token in members:
            (notice,) = _feed(api_client, token)
            assert notice["read"] is False
            assert notice["contextMetadata"]["announcementId"] == published["id"]
            assert notice["contextMetadata"]["spaceId"] == space_id
        assert _feed(api_client, owner) == []

    def test_the_publish_dialog_counts_the_people_it_will_notify(
        self, user_client: UserCreator, api_client: TestClient
    ):
        space_id, owner, members = _space_with_members(user_client, api_client, 3)
        assert _listed(api_client, owner, space_id)["notifyCount"] == 3
        assert _listed(api_client, members[0], space_id)["notifyCount"] is None

    def test_it_queues_a_digest_and_no_push(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        db_session: AsyncSession,
        _portal,
    ):
        space_id, owner, members = _space_with_members(user_client, api_client, 2)

        async def _channels() -> list[str]:
            return list(await db_session.scalars(select(ChannelDelivery.channel)))

        before = _portal.call(_channels)
        _publish(api_client, owner, space_id)
        after = _portal.call(_channels)
        # 设计稿「空间公告」默认站内 + 邮件（折进摘要）、不推送：每位成员一条摘要行。
        assert after[len(before) :] == ["digest"] * len(members)

    def test_editing_notifies_nobody_again(
        self, user_client: UserCreator, api_client: TestClient
    ):
        space_id, owner, (member,) = _space_with_members(user_client, api_client, 1)
        published = _publish(api_client, owner, space_id)
        resp = api_client.patch(
            f"/spaces/{space_id}/announcements/{published['id']}",
            json={"title": "期中报告统一提交 PDF（已更正）", "pinned": True},
            headers=_auth(owner),
        )
        assert resp.status_code == 200, resp.text

        (notice,) = _feed(api_client, member)
        # The one notice there is says what the announcement says now.
        assert notice["contextMetadata"]["title"] == "期中报告统一提交 PDF（已更正）"

    def test_deleting_takes_its_notices_away(
        self, user_client: UserCreator, api_client: TestClient
    ):
        space_id, owner, members = _space_with_members(user_client, api_client, 2)
        kept = _publish(api_client, owner, space_id, title="留着的")
        gone = _publish(api_client, owner, space_id, title="删掉的")

        resp = api_client.delete(
            f"/spaces/{space_id}/announcements/{gone['id']}", headers=_auth(owner)
        )
        assert resp.status_code == 204, resp.text

        for token in members:
            (notice,) = _feed(api_client, token)
            assert notice["contextMetadata"]["announcementId"] == kept["id"]
        assert [a["id"] for a in _listed(api_client, owner, space_id)["current"]] == [
            kept["id"]
        ]


class TestCurrentAndExpired:
    def test_an_expired_announcement_is_not_current(
        self, user_client: UserCreator, api_client: TestClient
    ):
        space_id, owner, (member,) = _space_with_members(user_client, api_client, 1)
        soon = int(time.time() * 1000) + 1500
        expiring = _publish(
            api_client, owner, space_id, title="快到期的", pinned=True, expiresAt=soon
        )
        lasting = _publish(api_client, owner, space_id, title="不到期的", pinned=True)

        time.sleep(2)

        listed = _listed(api_client, member, space_id)
        assert [a["id"] for a in listed["current"]] == [lasting["id"]]
        assert [a["id"] for a in listed["expired"]] == [expiring["id"]]

    def test_pinned_come_first_then_newest(
        self, user_client: UserCreator, api_client: TestClient
    ):
        space_id, owner, (member,) = _space_with_members(user_client, api_client, 1)
        old_pinned = _publish(api_client, owner, space_id, title="置顶", pinned=True)
        older = _publish(api_client, owner, space_id, title="普通一")
        newer = _publish(api_client, owner, space_id, title="普通二")

        listed = _listed(api_client, member, space_id)
        assert [a["id"] for a in listed["current"]] == [
            old_pinned["id"],
            newer["id"],
            older["id"],
        ]

    def test_publishing_something_already_expired_is_refused(
        self, user_client: UserCreator, api_client: TestClient
    ):
        space_id, owner, (member,) = _space_with_members(user_client, api_client, 1)
        resp = api_client.post(
            f"/spaces/{space_id}/announcements",
            json={"title": "昨天就到期", "expiresAt": int(time.time() * 1000) - 1000},
            headers=_auth(owner),
        )
        assert resp.status_code == 400, resp.text
        assert _feed(api_client, member) == []


class TestTwoManagersAtOnce:
    def test_editing_one_announcement_leaves_the_other_alone(
        self, user_client: UserCreator, api_client: TestClient
    ):
        space_id, owner, (member,) = _space_with_members(user_client, api_client, 1)
        first = _publish(api_client, owner, space_id, title="第一条")
        second = _publish(api_client, owner, space_id, title="第二条")

        for target, title in ((first, "第一条改过"), (second, "第二条改过")):
            resp = api_client.patch(
                f"/spaces/{space_id}/announcements/{target['id']}",
                json={"title": title},
                headers=_auth(owner),
            )
            assert resp.status_code == 200, resp.text

        titles = {a["title"] for a in _listed(api_client, member, space_id)["current"]}
        assert titles == {"第一条改过", "第二条改过"}
