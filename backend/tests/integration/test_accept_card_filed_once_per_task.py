"""一条任务同一时刻只有一张未决的验收卡，两次递卡撞在一起也一样 (FB-108)。

递卡先查「这条任务有没有未决的卡」，再去 forge 读分支差异，最后落卡。查和落之间
隔着一次网络读：客户端读超时后重试，第二个请求会在第一个还没提交时查到「没有」，
两边各落一张。这里让第一个请求停在读差异那一步，第二个请求在它停着的时候进来。
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.core.errors import ValidationError
from app.domain.agent.announce import announce
from app.domain.agent_instance.adapters import project_team
from app.domain.delivery.agent import record_task_instruction
from app.domain.repository.forge_files import ProjectFiles
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.services import AcceptService
from app.domain.review.services._shared import _BLOCKED_BY_CARD_MESSAGES
from tests.integration.test_accept_pr import app_world as app_world
from tests.integration.test_tree_sealing_and_quick_check import (
    _admits_anyone,
    _room,
    _task,
)
from tests.support.review_ports import settle_card, task_teammate

#: 第二个请求要是没被挡住，会在这么久里走到读差异那一步。
_SECOND_REACHES_FORGE = 2.0


def _service(session) -> AcceptService:
    return AcceptService(
        session,
        announce=announce,
        settle_card=settle_card,
        record_instruction=record_task_instruction,
        task_teammate=task_teammate,
        roster_team=project_team,
    )


async def _file(client, room: uuid.UUID, task_id: uuid.UUID) -> AcceptCard:
    """一次递卡请求：自己的会话、自己的事务，和路由一样落卡后才提交。"""
    async with client.test_factory() as session:
        card = await _service(session).create_card(
            topic_id=room,
            task_id=task_id,
            reviewer_handles=["alice"],
            change_subject="feat: add the result",
            admits_reviewer=_admits_anyone,
        )
        await session.commit()
        return card


async def _pending_cards(client, task_id: uuid.UUID) -> list[AcceptCard]:
    async with client.test_factory() as session:
        rows = await session.execute(
            select(AcceptCard).where(
                AcceptCard.task_id == task_id,
                AcceptCard.status == AcceptStatus.pending,
            )
        )
        return list(rows.scalars())


def _open_task(client) -> tuple[uuid.UUID, uuid.UUID]:
    project, room = _room(client)

    async def run():
        async with client.test_factory() as session:
            task = await _task(session, project, room, "deliver")
            await session.commit()
            return task.id

    return room, client.portal.call(run)


def test_a_second_filing_while_the_first_reads_the_branch_is_refused(
    client, app_world, monkeypatch
):
    room, task_id = _open_task(client)
    first_at_forge = asyncio.Event()
    second_at_forge = asyncio.Event()
    # 递卡这一路落卡后还会再读差异（迁移撞号的提醒），所以按是哪个请求在读来分，
    # 不按第几次读。
    first_request: list[asyncio.Task] = []

    async def comparison(self):
        me = asyncio.current_task()
        if not first_request:
            first_request.append(me)
            first_at_forge.set()
            # 第一个请求停在这里，直到第二个也走到这一步，或者等满了还没来。
            try:
                await asyncio.wait_for(second_at_forge.wait(), _SECOND_REACHES_FORGE)
            except TimeoutError:
                pass
        elif me is not first_request[0]:
            second_at_forge.set()
        return {"total_commits": 1, "files": [{"filename": "a.txt"}]}

    monkeypatch.setattr(ProjectFiles, "comparison", comparison)

    async def race():
        first = asyncio.create_task(_file(client, room, task_id))
        await first_at_forge.wait()
        second = asyncio.create_task(_file(client, room, task_id))
        return await asyncio.gather(first, second, return_exceptions=True)

    first, second = client.portal.call(race)

    assert isinstance(first, AcceptCard)
    assert isinstance(second, ValidationError)
    assert _BLOCKED_BY_CARD_MESSAGES[AcceptStatus.pending] in str(second)
    # 被挡住的那次没走到 forge：它是在第一张卡提交之后才查的。
    assert not second_at_forge.is_set()
    pending = client.portal.call(_pending_cards, client, task_id)
    assert [c.id for c in pending] == [first.id]


def test_a_retry_after_the_first_filing_landed_is_refused(
    client, app_world, monkeypatch
):
    room, task_id = _open_task(client)

    async def comparison(self):
        return {"total_commits": 1, "files": [{"filename": "a.txt"}]}

    monkeypatch.setattr(ProjectFiles, "comparison", comparison)

    first = client.portal.call(_file, client, room, task_id)
    with pytest.raises(ValidationError) as refused:
        client.portal.call(_file, client, room, task_id)

    assert _BLOCKED_BY_CARD_MESSAGES[AcceptStatus.pending] in str(refused.value)
    pending = client.portal.call(_pending_cards, client, task_id)
    assert [c.id for c in pending] == [first.id]
