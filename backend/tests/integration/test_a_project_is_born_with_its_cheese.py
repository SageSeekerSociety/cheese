"""建一个项目，它的芝士和它在总览里的席位一起出生。

以前项目可以没有芝士这一行：`IMPLICIT_DEFAULT` 是一个 `instance_id=None` 的隐式
默认，靠 handle 蹭出同一个记忆池。那是一份没有行的参与者——它坐不进名册，授权没
法按席位回答，房间里问「谁答这一句」得绕回项目字段。所以建项目时就播种：一行实例，
一条总览房间的席位，同一个事务里落。

「建了项目就有」不能只看实例表：席位要能被当成 agent 的席位认出来（它得有自己的
用户行和 execution binding），否则名册上多一行字符串，房间照样退回房间派生的身份。
"""

import uuid

from app.domain.agent_instance.repositories import AgentInstanceRepository
from app.domain.identity.handles import CHEESE_HANDLE, agent_instance_handle
from app.domain.project.repositories import ProjectRepository
from app.domain.topic_membership.services import TopicMemberService

OWNER = "owner-born"


def _create_project(client, name: str = "P") -> uuid.UUID:
    body = client.post("/projects", json={"name": name, "owner_handle": OWNER}).json()
    return uuid.UUID(body["data"]["id"])


async def test_a_new_project_has_one_cheese_instance(client):
    """项目一建出来就有一行芝士实例，而且它就是新房间的默认。"""
    pid = _create_project(client)

    async with client.test_factory() as session:
        project = await ProjectRepository(session).get(pid)
        assert project is not None
        rows = await AgentInstanceRepository(session).list_for_project(pid)
        assert [row.handle for row in rows] == [CHEESE_HANDLE]
        assert project.default_agent_instance_id == rows[0].id


async def test_the_new_project_seats_its_cheese_in_the_root_room(client):
    """那一行芝士在总览房间里有自己的席位，而且是按 agent 认出来的那种席位。

    `agent_handles` 读的是 execution binding，不是 handle 的样子——所以这条断言
    同时守住「实例有身份」和「席位是它的」两件事。
    """
    pid = _create_project(client)

    async with client.test_factory() as session:
        project = await ProjectRepository(session).get(pid)
        assert project is not None and project.root_topic_id is not None
        rows = await AgentInstanceRepository(session).list_for_project(pid)
        seat = agent_instance_handle(rows[0].id)

        members = TopicMemberService(session)
        roster, _ = await members.list_for_topic(project.root_topic_id)
        assert seat in {row.member_handle for row in roster}
        assert await members.agent_handles(project.root_topic_id) == [seat]
        assert await members.resolve_agent_handle(project.root_topic_id) == seat
