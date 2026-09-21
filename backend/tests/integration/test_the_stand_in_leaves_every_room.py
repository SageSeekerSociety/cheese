"""名册上只坐着存量替身的老房间，席位换成项目自己那位芝士。

``aeb21133e`` 之前建的房间，名册上坐的是那间房派生出来的替身
``cheese-<房间 hex12>``：有自己的用户行和 execution binding，所以名册认它是 agent，
而项目自己那位芝士在这些房里没有席位。``b4d1a70c9e52`` 只解析总览，非总览的老房间
一行没动。

从这条迁移起名册就是全部答案：``holds_an_agent_seat`` 不再给项目凭证留例外，
``resolve_agent_handle`` 也只从席位上取名字。所以这批房间不搬过来的话，线下那张项目
凭证在每一间里都会从 200 变 403，而署名、commit identity、令牌的 ``a`` 会继续用房间
派生的名字——「做选择的代码」换了形状，库里选中的还是旧名字。

跑的是迁移自己的 ``retire_stand_ins``（从迁移模块 import），不是照抄一份顺序。
"""

import importlib.util
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.identity.services import IdentityService
from app.domain.membership.repositories import MemberRepository
from app.domain.project.models import ProjectRole
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

if TYPE_CHECKING:
    from anyio.from_thread import BlockingPortal

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "d5c48f1a6b73_an_agent_signs_with_its_instance_handle.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("_agent_signs", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stand_in(topic_id: uuid.UUID) -> str:
    """旧代码从**房间**派生出来的那个 handle。铸它的函数已经删了，所以这里逐字写出
    库里存着的字符串：这些用例面对的就是存量行。"""
    return f"cheese-{topic_id.hex[:12]}"


async def _age(session: AsyncSession, room_id: uuid.UUID, own: str) -> str:
    """把一间房退回 ``aeb21133e`` 之前的样子：项目芝士的席位没有，坐着的是替身。"""
    await session.execute(
        sa.text("DELETE FROM topic_memberships WHERE topic_id=:t AND member_handle=:h"),
        {"t": room_id, "h": own},
    )
    stand_in = _stand_in(room_id)
    await IdentityService(session).ensure_agent_user(handle=stand_in)
    await TopicMemberService(session).ensure_agent_seat(room_id, stand_in)
    return stand_in


async def _say(
    session: AsyncSession, *, project_id: uuid.UUID, topic_id: uuid.UUID, author: str
) -> uuid.UUID:
    block_id = uuid.uuid4()
    await session.execute(
        sa.text(
            "INSERT INTO blocks (id, project_id, topic_id, kind, author_type,"
            " author, content, doc_version, refs, created_at, updated_at)"
            " VALUES (:id, :p, :t, 'message', 'participant', :a, '这句是替身说的',"
            " 1, CAST('[]' AS json), now(), now())"
        ),
        {"id": block_id, "p": project_id, "t": topic_id, "a": author},
    )
    return block_id


async def _retire(session: AsyncSession) -> None:
    """迁移里替身退役那一段，原样跑一遍——调的是 ``retire_stand_ins`` 本人。

    照着抄一份顺序，抄出来的那份就会和真要发布的这份走散，而走散的地方恰恰是用例看
    不见的地方。``exec_driver_sql`` 收的是裸 SQL，和迁移里的 ``op.execute`` 一样，
    所以 ``':cheese-'`` 不会被当成绑定参数。
    """
    module = _migration()
    connection = await session.connection()
    await connection.run_sync(
        lambda sync: module.retire_stand_ins(sync.exec_driver_sql)
    )
    await session.flush()


def test_an_old_rooms_seat_and_lines_move_to_the_projects_cheese(
    db_session: AsyncSession, _portal: "BlockingPortal"
) -> None:
    """替身的席位没了，它说过的话记在芝士自己名下，项目凭证在这间房里还进得来。"""

    async def run() -> None:
        members = TopicMemberService(db_session)
        project = await ProjectService(db_session).create(
            name="Old room", forge_kind="github_app"
        )
        room = await TopicService(db_session).create(
            project_id=project.id, title="老房间", created_by="alice"
        )
        own = agent_instance_handle(project.default_agent_instance_id)
        stand_in = await _age(db_session, room.id, own)
        said = await _say(
            db_session, project_id=project.id, topic_id=room.id, author=stand_in
        )
        await db_session.flush()
        # 迁移之前：名册上只有替身，「我是谁」答出来的也是它。
        assert await members.resolve_agent_handle(room.id) == stand_in
        assert not await members.holds_an_agent_seat(room, own)

        await _retire(db_session)

        roster = {m.member_handle for m in (await members.list_for_topic(room.id))[0]}
        assert own in roster
        assert stand_in not in roster
        assert await members.resolve_agent_handle(room.id) == own
        assert await members.holds_an_agent_seat(room, own)
        assert (
            await db_session.scalar(
                sa.text("SELECT author FROM blocks WHERE id=:id"), {"id": said}
            )
            == own
        )

    _portal.call(run)


def test_a_room_seating_another_agent_keeps_its_stand_in(
    db_session: AsyncSession, _portal: "BlockingPortal"
) -> None:
    """房间里还坐着别的 agent：说不出替身站的是哪一个，署名和替身那一行都不动。

    席位是另一个问题，而那个问题在这里没有歧义：名册从今天起就是全部答案，项目的
    芝士没有席位就是 403，所以它照样补上——线下那张项目凭证在这间房里进得来，替身
    则留在原地等人来说它站的是谁。
    """

    async def run() -> None:
        members = TopicMemberService(db_session)
        project = await ProjectService(db_session).create(
            name="Crowded old room", forge_kind="github_app"
        )
        room = await TopicService(db_session).create(
            project_id=project.id, title="老房间", created_by="alice"
        )
        own = agent_instance_handle(project.default_agent_instance_id)
        other = await AgentInstanceService(db_session).create(
            project_id=project.id,
            handle="reviewer",
            type_name=None,
            display_name="评审",
        )
        await members.ensure_agent_seat(room.id, agent_instance_handle(other.id))
        stand_in = await _age(db_session, room.id, own)
        said = await _say(
            db_session, project_id=project.id, topic_id=room.id, author=stand_in
        )
        await db_session.flush()

        await _retire(db_session)

        roster = {m.member_handle for m in (await members.list_for_topic(room.id))[0]}
        assert stand_in in roster
        assert await members.holds_an_agent_seat(room, own)
        said_by_the_stand_in = await db_session.scalar(
            sa.text("SELECT author FROM blocks WHERE id=:id"), {"id": said}
        )
        assert said_by_the_stand_in == stand_in

    _portal.call(run)


def test_the_project_roster_row_follows_the_credentials_new_name(
    db_session: AsyncSession, _portal: "BlockingPortal"
) -> None:
    """项目名册上发给旧 handle 的那一行，重指到芝士自己名下。

    项目凭证以前认证成 ``cheese-<根房间 hex12>``；换了名字而名册那一行不动，它的
    项目级访问就作废了，还没有任何报错指向原因。
    """

    async def run() -> None:
        project = await ProjectService(db_session).create(
            name="Off-platform 芝士", forge_kind="github_app"
        )
        assert project.root_topic_id is not None
        own = agent_instance_handle(project.default_agent_instance_id)
        members = MemberRepository(db_session)
        await members.add(
            project_id=project.id,
            user_handle=_stand_in(project.root_topic_id),
            role=ProjectRole.member,
        )
        await db_session.flush()

        await _retire(db_session)

        handles = {m.user_handle for m in await members.list_for_project(project.id)}
        assert own in handles
        assert _stand_in(project.root_topic_id) not in handles

    _portal.call(run)
