"""A channel's creator keeps managing it until ownership is handed to someone else."""

import pytest

from app.domain.topic_membership import services


@pytest.mark.anyio
async def test_creator_retains_channel_ownership_when_no_owner_seat_remains(
    monkeypatch,
):
    """频道创建者退出或以 member 席位重进后，只要频道没有显式转交新的 owner，
    创建者仍然是频道的管理者，能够归档该频道。"""
    import uuid
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.domain.topic.models import TopicKind, TopicRole

    topic_id = uuid.uuid4()
    project_id = uuid.uuid4()
    now = datetime.now(UTC)
    topic = SimpleNamespace(
        id=topic_id,
        project_id=project_id,
        kind=TopicKind.topic,
        is_private=False,
        members_only=False,
        created_by="caisongyang",
        created_at=now,
    )

    svc = services.TopicMemberService.__new__(services.TopicMemberService)
    svc._session = SimpleNamespace(flush=AsyncMock())
    svc._topics = SimpleNamespace(get=AsyncMock(return_value=topic))
    # 「这个人管得着这个项目吗」现在直接问 `may`（它自己读库的那一步在这里用
    # 不了），不再经 `MemberService.manages` 兜一层：打桩打在代码真正用到的那
    # 个名字上。这条用例问的是频道自己的归属兜底，项目权限一律答「不」。
    monkeypatch.setattr(services, "may", AsyncMock(return_value=False))
    svc._repo = SimpleNamespace(
        list_for_topic=AsyncMock(
            return_value=[
                SimpleNamespace(
                    topic_id=topic_id,
                    member_handle="caisongyang",
                    role=TopicRole.member,
                    created_at=now,
                )
            ]
        ),
        topics_with_owner=AsyncMock(return_value=set()),
        topic_ids_for_member=AsyncMock(return_value=set()),
        get=AsyncMock(return_value=None),
        add=AsyncMock(),
    )

    team = SimpleNamespace(people_in=AsyncMock(return_value=["caisongyang"]))
    assert await svc.owner_of(topic_id) == "caisongyang"
    roster = await svc.seats(topic, team=team)
    assert [(s.member_handle, s.role) for s in roster] == [
        ("caisongyang", TopicRole.owner)
    ]
    assert await svc.manages(topic, "caisongyang") is True
    assert await svc.managed_topic_ids([topic], "caisongyang") == {topic_id}
    assert await svc.administers(topic, "caisongyang") is True

    # 显式转交给别人后，原创建者不再兜底为 owner
    svc._repo.list_for_topic = AsyncMock(
        return_value=[
            SimpleNamespace(member_handle="bob", role=TopicRole.owner),
            SimpleNamespace(member_handle="caisongyang", role=TopicRole.member),
        ]
    )
    svc._repo.topics_with_owner = AsyncMock(return_value={topic_id})
    assert await svc.owner_of(topic_id) == "bob"
    assert await svc.manages(topic, "caisongyang") is False
    assert await svc.managed_topic_ids([topic], "caisongyang") == set()
