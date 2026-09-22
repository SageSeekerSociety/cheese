"""``topics`` 上再没有 ``private_owner`` 与 ``private_peer`` 两列（结论 19）。

私聊是项目内名册两席的房间，「对面是谁」只有名册一个出处
（``TopicMemberService.private_seats``）。#1386 那一版起没有代码再读写这两列，可
它们还留在表上——一列旧答案，查得到就有人信，而它和名册对不上的时候对的从来是名
册。留着的那份声明正是「对面是谁」重新有两个住处的入口（I4a）。

问的是库，不是代码：一个跑完全部迁移的 ``topics``，这两列必须已经不在。代码那一
侧的守卫在 ``tests/unit/test_is_private_read_points.py``。
"""

import sqlalchemy as sa

#: 退役的两列。名字写死在这里，不从别处读：这条用例的全部内容就是它们不在了。
RETIRED = ("private_owner", "private_peer")


def test_topics_no_longer_carries_the_two_columns(db_session, _portal):
    async def run() -> set[str]:
        rows = await db_session.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = current_schema()"
                "   AND table_name = 'topics'"
                "   AND column_name = ANY(:names)"
            ),
            {"names": list(RETIRED)},
        )
        return {row[0] for row in rows}

    assert _portal.call(run) == set(), (
        "``topics`` 上还留着退役的列：私聊的两席只住在 ``topic_memberships`` 上"
        "（结论 19），表上再有一份就是第二个出处。"
    )
