"""回填席位的那一刻，总览里的房间派生替身也就退了役。

上一条迁移 `f3a8c5d2e917` 解析不了「项目没有默认 agent」的房间——没有默认就说不出
替身站的是谁——所以那批总览至今坐着 `cheese-<root topic hex>`。这一条给同一批项目
补上芝士实例和它的席位；替身要是留着，一个名册上就坐着两个芝士，而且撤掉实例席位
也不再关得上写闸门：替身自己也带 execution binding，名册照样回答「这里有个 agent」。

留着不动的是另一种房间：总览里还坐着别的 agent，那就说不出替身站的是哪一个，和
`f3a8c5d2e917` 当时的判断一致。

退了役就不许自己回来：房间派生的 handle 已经没有铸造它的代码了（agent 的名字
只从 agent 自己来），所以房间在 agent 开口前自己迁移共享席位那一步
（`migrate_shared_agent_seat`）撞见总览上遗留的裸 `cheese` 行时只是把它删掉，
补席位补的是这个项目自己那位芝士。替身只作为库里的存量出现在这里。

前两个用例在迁移前一版的库上播种存量（没有默认 agent 的项目，总览坐着替身），
升到这条迁移，再读名册和署名；后两个用例走的是今天的服务，不重放迁移。
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from app.domain.identity.handles import CHEESE_HANDLE, agent_instance_handle
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from tests.integration.conftest import registered
from tests.integration.migration_replay import (
    block_author,
    database_at,
    roster,
    seat_agent,
    seat_handle,
    seed_agent,
    seed_agent_project,
    seed_block,
    stand_in_handle,
)

BEFORE = "c1a7e05d4b83"
AFTER = "b4d1a70c9e52"


def _stand_in(topic_id) -> str:
    """旧代码从**房间**派生出来的那个 handle。

    铸它的函数已经删了（一间房可以坐好几个 agent，从房间派生会给它们同一个名字），
    所以这里逐字写出库里存着的那个字符串：这些用例面对的就是存量行。
    """
    return stand_in_handle(topic_id)


def test_the_overview_ends_up_with_one_cheese_and_its_lines():
    """替身的席位没了，它说过的话记在芝士自己的席位下。"""
    with database_at(BEFORE) as db:
        # 一个从没选过 agent 的项目：没有实例行，没有指针，总览坐着替身。
        project, root, _ = seed_agent_project(db, default_agent=False)
        stand_in = _stand_in(root)
        seat_agent(db, root, stand_in)
        said = seed_block(
            db, project, root, turn=None, at=datetime.now(UTC), author=stand_in
        )

        db.upgrade(AFTER)

        own = seat_handle(
            db.fetchval(
                "SELECT default_agent_instance_id FROM projects WHERE id = $1",
                project,
            )
        )
        seated = roster(db, root)
        assert own in seated
        assert stand_in not in seated
        assert block_author(db, said) == own


def test_a_room_seating_another_agent_keeps_its_stand_in():
    """总览里还坐着别的 agent：说不出替身站的是谁，所以一行都不动。"""
    with database_at(BEFORE) as db:
        project, root, _ = seed_agent_project(db, default_agent=False)
        reviewer = seed_agent(db, project, "reviewer")
        seat_agent(db, root, seat_handle(reviewer))
        stand_in = _stand_in(root)
        seat_agent(db, root, stand_in)
        said = seed_block(
            db, project, root, turn=None, at=datetime.now(UTC), author=stand_in
        )

        db.upgrade(AFTER)

        assert stand_in in roster(db, root)
        assert block_author(db, said) == stand_in


async def _drop_seat(session, topic_id: uuid.UUID, handle: str) -> None:
    await session.execute(
        sa.text("DELETE FROM topic_memberships WHERE topic_id=:t AND member_handle=:h"),
        {"t": topic_id, "h": handle},
    )


def test_migrating_a_shared_seat_does_not_re_seat_the_stand_in(db_session, _portal):
    """旧房间自己迁移的那一步，不许把替身种回总览。

    总览上已经坐着这个项目自己的芝士，所以删掉裸 `cheese` 行就收工：补席位这一步
    看到房间里还有 agent 就不动手。补种真的发生时补的也是同一位芝士——房间派生的
    名字已经没有地方能造出来了。
    """

    async def run():
        members = TopicMemberService(db_session)
        await registered(db_session, "owner")
        project = await ProjectService(db_session).create(
            owner_handle="owner",
            name="Shared seat on the overview",
            forge_kind="github_app",
        )
        root = project.root_topic_id
        own = agent_instance_handle(project.default_agent_instance_id)
        # 回填没有动过的那一行：总览上的裸 `cheese`，旧代码的名册留下的。
        await members.ensure_agent_seat(root, CHEESE_HANDLE)

        await members.migrate_shared_agent_seat(root)

        roster = {m.member_handle for m in (await members.list_for_topic(root))[0]}
        assert CHEESE_HANDLE not in roster
        assert _stand_in(root) not in roster
        assert roster & {own} == {own}
        assert await members.resolve_agent_handle(root) == own

    _portal.call(run)


def test_a_room_whose_last_agent_was_the_shared_seat_gets_the_projects_cheese(
    db_session, _portal
):
    """裸 `cheese` 是这个房间最后一个 agent 席位时，换上的是项目自己那位芝士。

    换的不是一个按房间派生的名字：那样的话同一位芝士在两个房间里会有两个名字，
    它在这里签的字和它在别处签的字就对不上了。"""

    async def run():
        members = TopicMemberService(db_session)
        await registered(db_session, "owner")
        project = await ProjectService(db_session).create(
            owner_handle="owner", name="Legacy room", forge_kind="github_app"
        )
        room = await TopicService(db_session).create(
            project_id=project.id, title="旧房间", created_by="alice"
        )
        # 分身出现之前的名册长这样：创建者，加上平台账号那一个共享席位。
        await _drop_seat(
            db_session,
            room.id,
            agent_instance_handle(project.default_agent_instance_id),
        )
        await members.ensure_agent_seat(room.id, CHEESE_HANDLE)

        await members.migrate_shared_agent_seat(room.id)

        own = agent_instance_handle(project.default_agent_instance_id)
        roster = {m.member_handle for m in (await members.list_for_topic(room.id))[0]}
        assert CHEESE_HANDLE not in roster
        assert _stand_in(room.id) not in roster
        assert own in roster

    _portal.call(run)
