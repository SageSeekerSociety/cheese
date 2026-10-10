"""「开始清单」第一步「跟芝士说第一句话」：这个人在项目里跟 AI 队友说上过话没有。

说上话可以发生在项目里任何一段对话里——频道主线、支线、任务里的对话。频道那
一栏读不到任务对话，所以这件事由服务端按整个项目回答。
"""

import uuid

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.models import ProjectMember
from app.domain.room_task.services import TaskService
from app.domain.topic.models import Topic
from tests.integration.conftest import post_project, session_auth_headers


def _project(client) -> str:
    return post_project(
        client, json={"name": "报名表"}, headers=session_auth_headers("alice")
    ).json()["data"]["id"]


def _channel(client, project: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project, "title": "前端"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _task(client, channel: str) -> str:
    async def seed() -> uuid.UUID:
        async with client.test_factory() as session:
            room = await session.get(Topic, uuid.UUID(channel))
            task = await TaskService(session).open_thread(
                project_id=room.project_id,
                room_id=room.id,
                title="报名表加一列手机号",
                owner_handle="alice",
                created_by="alice",
            )
            await session.commit()
            return task.id

    return str(client.portal.call(seed))


def _member(client, project: str, handle: str) -> None:
    async def seed() -> None:
        async with client.test_factory() as session:
            session.add(
                ProjectMember(project_id=uuid.UUID(project), user_handle=handle)
            )
            await session.commit()

    client.portal.call(seed)


def _say(
    client,
    project: str,
    place: str,
    author: str,
    *,
    author_type: AuthorType = AuthorType.participant,
) -> None:
    async def seed() -> None:
        async with client.test_factory() as session:
            await BlockRepository(session).add(
                project_id=uuid.UUID(project),
                conversation_id=uuid.UUID(place),
                author=author,
                author_type=author_type,
                content="说了一句",
                kind=BlockKind.message,
            )
            await session.commit()

    client.portal.call(seed)


def _talked(client, project: str, handle: str) -> bool:
    r = client.get(
        f"/projects/{project}/getting-started", headers=session_auth_headers(handle)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["talked"]


def test_a_new_project_has_not_talked_to_its_teammate(client):
    project = _project(client)
    assert _talked(client, project, "alice") is False


def test_a_conversation_inside_a_task_counts(client):
    project = _project(client)
    task = _task(client, _channel(client, project))
    _member(client, project, "bobby")
    _say(client, project, task, "alice")
    _say(client, project, task, "cheese")

    assert _talked(client, project, "alice") is True
    # 只有说过话的那个人算：同项目里一句没说的人还没开过口。
    assert _talked(client, project, "bobby") is False


def test_only_a_reply_in_a_conversation_the_person_spoke_in_counts(client):
    project = _project(client)
    channel = _channel(client, project)
    task = _task(client, channel)
    _say(client, project, channel, "alice")
    # 芝士在别处说的话不算跟他说上了。
    _say(client, project, task, "cheese")
    assert _talked(client, project, "alice") is False

    # 平台以芝士的名义记的一行（提醒、闸门结论）也不是它开口回答。
    _say(client, project, channel, "cheese", author_type=AuthorType.platform)
    assert _talked(client, project, "alice") is False


# 「让成果进代码仓库」：仓库里合进过一次被采纳的改动。仓库接没接上不算——平台托管
# 的项目一建好就有仓库（dev，2026-10-10：往资料库放一个文件，这一步就打了勾）。


def _card(client, channel: str, *, merged: bool) -> None:
    from datetime import UTC, datetime

    from app.domain.review.models import AcceptCard, AcceptStatus

    async def seed() -> None:
        async with client.test_factory() as session:
            session.add(
                AcceptCard(
                    topic_id=uuid.UUID(channel),
                    reviewer_handle="alice",
                    status=AcceptStatus.accepted,
                    decided_by="alice",
                    decided_at=datetime.now(UTC),
                    pr_merged_at=datetime.now(UTC) if merged else None,
                )
            )
            await session.commit()

    client.portal.call(seed)


def _landed(client, project: str) -> bool:
    r = client.get(
        f"/projects/{project}/getting-started", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["landed"]


def test_a_new_project_has_landed_nothing_in_its_repository(client):
    assert _landed(client, _project(client)) is False


def test_an_accepted_change_merged_into_the_repository_counts(client):
    project = _project(client)
    _card(client, _channel(client, project), merged=True)
    assert _landed(client, project) is True


def test_an_acceptance_that_merged_nothing_does_not_count(client):
    project = _project(client)
    _card(client, _channel(client, project), merged=False)
    assert _landed(client, project) is False


def test_a_merge_in_another_project_does_not_count(client):
    project, other = _project(client), _project(client)
    _card(client, _channel(client, other), merged=True)
    assert _landed(client, project) is False
