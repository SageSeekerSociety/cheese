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
"""

import importlib.util
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import CHEESE_HANDLE, agent_instance_handle
from app.domain.identity.services import IdentityService
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService


def _migration():
    path = (
        Path(__file__).parents[2]
        / "alembic/versions/b4d1a70c9e52_a_project_has_its_cheese_and_its_seat.py"
    )
    spec = importlib.util.spec_from_file_location("project_cheese_seat", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stand_in(topic_id: uuid.UUID) -> str:
    """旧代码从**房间**派生出来的那个 handle。

    铸它的函数已经删了（一间房可以坐好几个 agent，从房间派生会给它们同一个名字），
    所以这里逐字写出库里存着的那个字符串：这些用例面对的就是存量行。
    """
    return f"cheese-{topic_id.hex[:12]}"


async def _seat_a_stand_in(session, topic_id: uuid.UUID) -> str:
    """把一条替身席位放回库里——用户行、execution binding、名册行，旧代码留下的
    就是这三样，缺了 binding 名册就不认它是 agent。"""
    handle = _stand_in(topic_id)
    await IdentityService(session).ensure_agent_user(handle=handle)
    await TopicMemberService(session).ensure_agent_seat(topic_id, handle)
    return handle


def _unseed(conn, *, project_id: uuid.UUID, root_id: uuid.UUID, seat: str) -> None:
    """把项目退回这条迁移之前的样子：没有实例行，总览里没有实例席位。"""
    conn.execute(
        sa.text("UPDATE projects SET default_agent_instance_id=NULL WHERE id=:p"),
        {"p": project_id},
    )
    conn.execute(
        sa.text("DELETE FROM topic_memberships WHERE topic_id=:t AND member_handle=:h"),
        {"t": root_id, "h": seat},
    )


def _say(conn, *, project_id: uuid.UUID, root_id: uuid.UUID, author: str) -> uuid.UUID:
    block_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO blocks (id, project_id, topic_id, kind, author_type,"
            " author, content, doc_version, refs, created_at, updated_at)"
            " VALUES (:id, :p, :t, 'message', 'participant', :a, '这句是替身说的',"
            " 1, CAST('[]' AS json), now(), now())"
        ),
        {"id": block_id, "p": project_id, "t": root_id, "a": author},
    )
    return block_id


def test_the_overview_ends_up_with_one_cheese_and_its_lines(db_session, _portal):
    """替身的席位没了，它说过的话记在芝士自己的席位下。"""
    migration = _migration()

    async def run():
        project = await ProjectService(db_session).create(
            name="Legacy", forge_kind="github_app"
        )
        root = project.root_topic_id
        seeded_seat = agent_instance_handle(project.default_agent_instance_id)
        # 替身席位是旧代码的 seed_root 留下的：有自己的用户行和 binding。
        stand_in = await _seat_a_stand_in(db_session, root)
        await db_session.flush()
        connection = await db_session.connection()

        def check(conn):
            migration.op = Operations(MigrationContext.configure(conn))
            _unseed(conn, project_id=project.id, root_id=root, seat=seeded_seat)
            conn.execute(
                sa.text("DELETE FROM agent_instances WHERE project_id=:p"),
                {"p": project.id},
            )
            said = _say(conn, project_id=project.id, root_id=root, author=stand_in)

            migration.upgrade()

            own = agent_instance_handle(
                conn.execute(
                    sa.text(
                        "SELECT default_agent_instance_id FROM projects WHERE id=:p"
                    ),
                    {"p": project.id},
                ).scalar_one()
            )
            roster = set(
                conn.execute(
                    sa.text(
                        "SELECT member_handle FROM topic_memberships WHERE topic_id=:t"
                    ),
                    {"t": root},
                ).scalars()
            )
            assert own in roster
            assert stand_in not in roster
            assert (
                conn.execute(
                    sa.text("SELECT author FROM blocks WHERE id=:id"), {"id": said}
                ).scalar_one()
                == own
            )

        await connection.run_sync(check)

    _portal.call(run)


def test_a_room_seating_another_agent_keeps_its_stand_in(db_session, _portal):
    """总览里还坐着别的 agent：说不出替身站的是谁，所以一行都不动。"""
    migration = _migration()

    async def run():
        agents = AgentInstanceService(db_session)
        project = await ProjectService(db_session).create(
            name="Crowded", forge_kind="github_app"
        )
        root = project.root_topic_id
        seeded_seat = agent_instance_handle(project.default_agent_instance_id)
        other = await agents.create(
            project_id=project.id,
            handle="reviewer",
            type_name=None,
            display_name="评审",
        )
        members = TopicMemberService(db_session)
        await members.ensure_agent_seat(root, agent_instance_handle(other.id))
        stand_in = await _seat_a_stand_in(db_session, root)
        await db_session.flush()
        connection = await db_session.connection()

        def check(conn):
            migration.op = Operations(MigrationContext.configure(conn))
            _unseed(conn, project_id=project.id, root_id=root, seat=seeded_seat)
            conn.execute(
                sa.text(
                    "DELETE FROM agent_instances"
                    " WHERE project_id=:p AND handle='cheese'"
                ),
                {"p": project.id},
            )
            said = _say(conn, project_id=project.id, root_id=root, author=stand_in)

            migration.upgrade()

            roster = set(
                conn.execute(
                    sa.text(
                        "SELECT member_handle FROM topic_memberships WHERE topic_id=:t"
                    ),
                    {"t": root},
                ).scalars()
            )
            assert stand_in in roster
            assert (
                conn.execute(
                    sa.text("SELECT author FROM blocks WHERE id=:id"), {"id": said}
                ).scalar_one()
                == stand_in
            )

        await connection.run_sync(check)

    _portal.call(run)


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
        project = await ProjectService(db_session).create(
            name="Shared seat on the overview", forge_kind="github_app"
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
        project = await ProjectService(db_session).create(
            name="Legacy room", forge_kind="github_app"
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
