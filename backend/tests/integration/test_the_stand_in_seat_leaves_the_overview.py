"""回填席位的那一刻，总览里的房间派生替身也就退了役。

上一条迁移 `f3a8c5d2e917` 解析不了「项目没有默认 agent」的房间——没有默认就说不出
替身站的是谁——所以那批总览至今坐着 `cheese-<root topic hex>`。这一条给同一批项目
补上芝士实例和它的席位；替身要是留着，一个名册上就坐着两个芝士，而且撤掉实例席位
也不再关得上写闸门：替身自己也带 execution binding，名册照样回答「这里有个 agent」。

留着不动的是另一种房间：总览里还坐着别的 agent，那就说不出替身站的是哪一个，和
`f3a8c5d2e917` 当时的判断一致。
"""

import importlib.util
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle, topic_agent_handle
from app.domain.project.services import ProjectService
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
        await TopicMemberService(db_session).ensure_topic_agent_seat(root)
        await db_session.flush()
        stand_in = topic_agent_handle(root)
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
        await members.ensure_topic_agent_seat(root)
        await db_session.flush()
        stand_in = topic_agent_handle(root)
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
