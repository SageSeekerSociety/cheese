"""跨领域调用的 service 接缝——把 repository 从别的领域手里收回来之后的兜底。

背景：`tests/unit/test_domain_import_guard.py` 禁止领域包直接 import 别的领域的
repository，正路是走对方的 service。这个文件测的就是那几条新接缝**行为没变**：

* ``ProjectService.get`` —— 判断「项目在不在」用它，不再自己构造
  ``ProjectRepository``
* ``AcceptService.open_pr_card_ids`` —— 调度器每轮拿它取待推进的卡，不再自己查
  ``AcceptCardRepository``
* ``device.wiring.sql_device_service`` —— agent / machine 要设备能力时的标准接线，
  不再自己 import ``device.sql_repository``

"""

import uuid
from datetime import UTC, datetime

import pytest

from app.domain.device.supply import Supply, Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.project.services import ProjectService
from app.domain.review.models import AcceptStatus
from app.domain.review.repositories import AcceptCardRepository
from app.domain.review.services import AcceptService
from app.domain.space.models import Space
from app.domain.topic.models import TopicStatus
from app.domain.topic.services import TopicService
from app.domain.user.models import User

pytestmark = pytest.mark.anyio


def _now() -> datetime:
    return datetime.now(UTC)


async def _space(session) -> int:
    space = Space(
        name=f"空间-{uuid.uuid4().hex[:6]}",
        intro="i",
        description="d",
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(space)
    await session.flush()
    return space.id



# ---------------------------------------------------------------------------
# ProjectService.get —— 服务层替掉了直接用 ProjectRepository
# ---------------------------------------------------------------------------


async def test_project_service_get_finds_project_and_returns_none_for_unknown(client):
    async with client.test_factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        await session.commit()
        pid = project.id

    async with client.test_factory() as session:
        service = ProjectService(session)
        found = await service.get(pid)
        assert found is not None
        assert found.id == pid
        # 不存在返回 None，不抛——调用方要的是分支
        assert await service.get(uuid.uuid4()) is None




# ---------------------------------------------------------------------------
# AcceptService.open_pr_card_ids —— 调度器用它替掉了 AcceptCardRepository
# ---------------------------------------------------------------------------


async def _card(session, topic_id, *, status: AcceptStatus):
    return await AcceptCardRepository(session).add(
        topic_id=topic_id,
        reviewer_handle="alice",
        routing_reason="",
        status=status,
    )


async def test_open_pr_card_ids_lists_only_cards_with_an_open_pr(client):
    async with client.test_factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topics = TopicService(session)
        t_open = await topics.create(project_id=project.id, title="A", created_by="u")
        t_other = await topics.create(project_id=project.id, title="B", created_by="u")
        t_done = await topics.create(project_id=project.id, title="C", created_by="u")
        open_card = await _card(session, t_open.id, status=AcceptStatus.pr_open)
        await _card(session, t_other.id, status=AcceptStatus.pending)
        await _card(session, t_done.id, status=AcceptStatus.accepted)
        await session.commit()
        open_id = open_card.id

    async with client.test_factory() as session:
        ids = await AcceptService(session).open_pr_card_ids()
        assert ids == [open_id]


async def test_open_pr_card_ids_skips_cards_on_archived_topics(client):
    """孤儿卡修复 (#291) 的判据也在这个接缝里：已归档话题上的卡不算「开着」。

    这里直接改话题状态，而不是走 ``TopicService.archive``——归档路径自己就会把卡关掉，
    要造的恰恰是它防的那种历史遗留行：卡还挂着 ``pr_open``，话题已经归档。轮询器要是
    还去推它，就是拿当初批准人的 GitHub token 去动没人跟的活儿。
    """
    async with client.test_factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topics = TopicService(session)
        t_live = await topics.create(
            project_id=project.id, title="活着", created_by="u"
        )
        t_gone = await topics.create(
            project_id=project.id, title="归档了", created_by="u"
        )
        live_card = await _card(session, t_live.id, status=AcceptStatus.pr_open)
        await _card(session, t_gone.id, status=AcceptStatus.pr_open)
        t_gone.status = TopicStatus.archived
        await session.commit()
        live_id = live_card.id

    async with client.test_factory() as session:
        assert await AcceptService(session).open_pr_card_ids() == [live_id]


async def test_open_pr_card_ids_is_empty_when_nothing_is_pending(client):
    async with client.test_factory() as session:
        ids = await AcceptService(session).open_pr_card_ids()
        assert ids == []


# ---------------------------------------------------------------------------
# device.wiring.sql_device_service —— agent / machine 的标准接线
# ---------------------------------------------------------------------------


async def test_sql_device_service_persists_the_flow_in_the_database(client):
    """工厂拿到的必须是 SQL 后端：换一个 session 还能读到同一个 flow。

    接错后端（比如内存实现）时这条会红——start 的 code 在新 session 里查不到。
    """
    async with client.test_factory() as session:
        user = User(
            username=f"dev-{uuid.uuid4().hex[:6]}",
            email=f"{uuid.uuid4().hex[:8]}@example.io",
            created_at=_now(),
            updated_at=_now(),
        )
        session.add(user)
        await session.flush()
        code = await sql_device_service(session).start("我的算力节点")
        await session.commit()
        owner_id = user.id

    # 换 session：pending
    async with client.test_factory() as session:
        assert (await sql_device_service(session).poll(code))["status"] == "pending"

    async with client.test_factory() as session:
        # `supply`/`visibility` 无默认值（#282 决定 2 / #358）：每个入口自己表态。
        # 这里是人拿 connector 注册自己那台常驻机器，所以是 self_hosted，且默认
        # isolated（未勾选「让它看到整台机器」）。
        device = await sql_device_service(session).approve(
            code,
            owner_user_id=owner_id,
            supply=Supply.self_hosted,
            visibility=Visibility.isolated,
        )
        await session.commit()
        device_id, token = device.device_id, device.token

    async with client.test_factory() as session:
        service = sql_device_service(session)
        polled = await service.poll(code)
        assert polled["status"] == "approved"
        assert polled["token"] == token
        # 同一个 token 能反查回这台设备
        verified = await service.verify_token(token)
        assert verified is not None
        assert verified.device_id == device_id
