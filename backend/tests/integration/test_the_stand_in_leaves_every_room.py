"""名册上只坐着存量替身的老房间，席位换成项目自己那位芝士。

``aeb21133e`` 之前建的房间，名册上坐的是那间房派生出来的替身
``cheese-<房间 hex12>``：有自己的用户行和 execution binding，所以名册认它是 agent，
而项目自己那位芝士在这些房里没有席位。``b4d1a70c9e52`` 只解析总览，非总览的老房间
一行没动。

从这条迁移起名册就是全部答案：项目凭证不再有例外，「我是谁」也只从席位上取名字。
所以这批房间不搬过来的话，线下那张项目凭证在每一间里都会从 200 变 403，而署名、
commit identity、令牌的 ``a`` 会继续用房间派生的名字——「做选择的代码」换了形状，
库里选中的还是旧名字。

每个用例在迁移前一版的库上播种存量行（名册、署名），升到这条迁移，再读名册和署名。
"""

from datetime import UTC, datetime

from tests.integration.migration_replay import (
    block_author,
    database_at,
    roster,
    seat_agent,
    seat_handle,
    seed_agent,
    seed_agent_project,
    seed_block,
    seed_extra_room,
    stand_in_handle,
)

BEFORE = "d3b8f1c72a94"
AFTER = "d5c48f1a6b73"


def _old_room(db, project):
    """一间 ``aeb21133e`` 之前建的房间：名册上只坐着替身，替身说过一句话。"""
    room = seed_extra_room(db, project)
    stand_in = stand_in_handle(room)
    seat_agent(db, room, stand_in)
    said = seed_block(
        db, project, room, turn=None, at=datetime.now(UTC), author=stand_in
    )
    return room, stand_in, said


def _is_an_agent(db, handle: str) -> bool:
    """名册认一个 handle 是 agent，靠的是它的用户行上挂着 execution binding。"""
    return bool(
        db.fetchval(
            'SELECT count(*) FROM agent_bindings b JOIN "user" u ON u.id = b.user_id'
            " WHERE u.username = $1",
            handle,
        )
    )


def test_an_old_rooms_seat_and_lines_move_to_the_projects_cheese():
    """替身的席位没了，它说过的话记在芝士自己名下，项目凭证在这间房里还进得来。"""
    with database_at(BEFORE) as db:
        project, _, agent = seed_agent_project(db)
        own = seat_handle(agent)
        room, stand_in, said = _old_room(db, project)
        # 迁移之前：名册上只有替身。
        assert roster(db, room) == {stand_in}

        db.upgrade(AFTER)

        seated = roster(db, room)
        assert own in seated
        assert stand_in not in seated
        assert _is_an_agent(db, own)
        assert block_author(db, said) == own


def test_a_room_seating_another_agent_keeps_its_stand_in():
    """房间里还坐着别的 agent：说不出替身站的是哪一个，署名和替身那一行都不动。

    席位是另一个问题，而那个问题在这里没有歧义：名册从今天起就是全部答案，项目的
    芝士没有席位就是 403，所以它照样补上——线下那张项目凭证在这间房里进得来，替身
    则留在原地等人来说它站的是谁。
    """
    with database_at(BEFORE) as db:
        project, _, agent = seed_agent_project(db)
        own = seat_handle(agent)
        room, stand_in, said = _old_room(db, project)
        reviewer = seed_agent(db, project, "reviewer")
        seat_agent(db, room, seat_handle(reviewer))

        db.upgrade(AFTER)

        seated = roster(db, room)
        assert stand_in in seated
        assert own in seated
        assert _is_an_agent(db, own)
        assert block_author(db, said) == stand_in


def test_the_project_roster_row_follows_the_credentials_new_name():
    """项目名册上发给旧 handle 的那一行，重指到芝士自己名下。

    项目凭证以前认证成 ``cheese-<根房间 hex12>``；换了名字而名册那一行不动，它的
    项目级访问就作废了，还没有任何报错指向原因。
    """
    with database_at(BEFORE) as db:
        project, root, agent = seed_agent_project(db)
        own = seat_handle(agent)
        db.execute(
            "INSERT INTO project_members (id, project_id, user_handle, role,"
            " created_at, updated_at)"
            " VALUES (gen_random_uuid(), $1, $2, 'member', now(), now())",
            project,
            stand_in_handle(root),
        )

        db.upgrade(AFTER)

        handles = {
            row["user_handle"]
            for row in db.fetch(
                "SELECT user_handle FROM project_members WHERE project_id = $1",
                project,
            )
        }
        assert own in handles
        assert stand_in_handle(root) not in handles
