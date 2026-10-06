"""未读角标两条路由：读的是别人的角标，走的是项目的门。

`GET /projects/{project_id}/topic-unread` 和 `/private-unread` 拿一个项目 id 取数，
却一度只问了「读谁的角标」（``resolve_recipient``），没问「你能不能看这个项目」
（``authorize_project``）—— 同一个项目上的 `GET /topics?project_id=` 是 403 的。

后果不是空手而归，而是一份可用的房间清单：非成员没有读游标，返回值里每条计数
恰好等于该房间的消息总数，于是这个接口同时回答了「有哪些房间」和「哪个房间最
热闹」。项目 id 是 UUID 猜不到，但项目 id 不是秘密（它出现在每一个房间链接里），
差的是那一道门。

本文件按「门在不在」来断，而不是按某个具体数字：

- 非成员（自己的合法凭据）→ 403，两条路由都是；
- 项目不存在 → 404（``authorize_project`` 的既有口径：先看成员，再看存在）；
- 凭据缺失/坏掉 → 401（``resolve_recipient`` 的既有口径，不许被这道门改掉）；
- 成员 → 照旧 200，角标还是他自己的那份。
"""

import asyncio
import uuid

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from tests.integration.conftest import post_project, session_auth_headers


def _project(client, owner: str = "user-1") -> str:
    r = post_project(client, json={"name": "Demo"}, owner=owner)
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _topic(client, project_id: str, title: str = "话题A") -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title},
        headers=session_auth_headers("user-1"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _add_member(client, project_id: str, handle: str) -> None:
    """Membership row directly: these tests are about the door, not the join flow."""
    from app.domain.project.models import ProjectMember

    async def _run() -> None:
        async with client.test_factory() as session:
            session.add(
                ProjectMember(project_id=uuid.UUID(project_id), user_handle=handle)
            )
            await session.commit()

    asyncio.run(_run())


def _seed_message(client, project_id: str, topic_id: str, author: str) -> None:
    async def _run() -> None:
        async with client.test_factory() as session:
            await BlockRepository(session).add(
                project_id=uuid.UUID(project_id),
                conversation_id=uuid.UUID(topic_id),
                author=author,
                author_type=AuthorType.participant,
                content="msg",
                kind=BlockKind.message,
            )
            await session.commit()

    asyncio.run(_run())


def _topic_unread(client, project_id: str, *, handle: str, headers: dict | None = None):
    return client.get(
        f"/projects/{project_id}/topic-unread",
        params={"handle": handle} if handle else None,
        headers=headers if headers is not None else session_auth_headers(handle),
    )


def _private_unread(
    client, project_id: str, *, handle: str, headers: dict | None = None
):
    return client.get(
        f"/projects/{project_id}/private-unread",
        params={"handle": handle} if handle else None,
        headers=headers if headers is not None else session_auth_headers(handle),
    )


def test_an_outsider_cannot_read_the_topic_badges_of_a_project(client):
    """陌生人拿着自己的合法凭据，连「这个项目有哪些房间」都不该拿到。

    他还是读得懂这个 map 的形状：非成员的读游标是空的，所以每条计数就是那个房间
    的消息总数 —— 响应本身就是一份房间清单加活跃度。
    """
    project_id = _project(client)
    topic_id = _topic(client, project_id)
    _add_member(client, project_id, "user-1")
    # 别人说的：自己的消息永远不算未读，作者得是别人这个角标才亮。
    _seed_message(client, project_id, topic_id, "cheese")

    # 对照：成员读得到自己的角标。
    own = _topic_unread(client, project_id, handle="user-1")
    assert own.status_code == 200, own.text
    assert own.json()["data"][topic_id]["messages"] == 1

    r = _topic_unread(client, project_id, handle="outsider-1")
    assert r.status_code == 403, r.text

    # 同一个项目上，别的读路由早就是这个口径了。
    r = client.get(
        "/topics",
        params={"project_id": project_id},
        headers=session_auth_headers("outsider-1"),
    )
    assert r.status_code == 403, r.text


def test_an_outsider_cannot_read_the_private_badges_of_a_project(client):
    project_id = _project(client)
    _add_member(client, project_id, "user-1")

    own = _private_unread(client, project_id, handle="user-1")
    assert own.status_code == 200, own.text

    r = _private_unread(client, project_id, handle="outsider-1")
    assert r.status_code == 403, r.text


def test_the_unread_routes_still_tell_missing_credentials_from_missing_projects(client):
    """两道既有的口径不能被这道门改掉。

    没有凭据仍然是 401（不是 403、更不是 200）；不存在的项目仍然是 404，而不是
    「你不是这个项目的成员」—— 那是关于一个不存在的项目的、无从支撑的断言。
    """
    project_id = _project(client)
    _add_member(client, project_id, "user-1")
    missing = str(uuid.uuid4())

    # 没有凭据：401，两条路由都是。
    assert _topic_unread(client, project_id, handle="", headers={}).status_code == 401
    assert _private_unread(client, project_id, handle="", headers={}).status_code == 401

    # 项目不存在：404，两条路由都是 —— 哪怕这个名字是陌生人的。
    r = _topic_unread(client, missing, handle="outsider-1")
    assert r.status_code == 404, r.text
    r = _private_unread(client, missing, handle="outsider-1")
    assert r.status_code == 404, r.text

    # 成员请求一个不存在的项目也是 404：门只认 id，不认人。
    r = _topic_unread(client, missing, handle="user-1")
    assert r.status_code == 404, r.text
