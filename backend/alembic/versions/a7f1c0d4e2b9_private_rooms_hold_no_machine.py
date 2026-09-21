"""私聊不占机器：删掉它们在 device_topic 上的绑定

Data-only migration —— 没有 schema 改动。

一间私聊的一轮不租手（结论 19），所以它不占任何机器。在这之前
`DeviceChannel._resolve_device_agent` 的私聊分支每一轮都把私聊的 `device_topic`
绑定刷到当时那台中心会话机上；那条分支随本次改动删了，库里已经写下的那些行就
没有人再写、也没有人再对。

留着它们不是无害的，因为读它的人还在，而且读的时候不问这一轮租没租手：

  * `agent/host_failure.py` 的 `judge_host_failure` 对任何话题都读
    `device_topic`，把 host-scoped 失败记在那一行指的机器上。会话机换过一次之后
    （新的私聊跑在 B 上，旧绑定仍写着 A），两次失败就会隔离 A，并在房间里说
    「机器 A 连续失败，已暂停派活」——而这一轮根本没用过 A。
  * `topic/retire.py` 的归档清理拿这一行去点名一台机器，要它交出这个房间的目录；
    那台机器上什么都没有，它离线时归档还会直接失败。

所以把私聊的行删掉：不占机器，这张表里就不该有它的行；私聊的草稿区开在这条会话
自己的机器上，那一位记在 `agent_sessions` 上，本来就不从这里读。删完没有人写得
回来——今天写 `device_topic` 的四处（`_resolve_device_agent`、`compute_configs`、
`topics.py` 的算力选择、`cloud_provider`）都在「这一轮要租手」那条路上，而私聊的
`needs_place` 恒为假。非私聊房间的绑定一行不动。

Revision ID: a7f1c0d4e2b9
Revises: a1286f09c001
Create Date: 2026-09-20 00:00:00.000000

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7f1c0d4e2b9"
down_revision: str | Sequence[str] | None = "a1286f09c001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.private_rooms_hold_no_machine")

_COUNT_SQL = sa.text(
    """
    SELECT count(*) AS n
      FROM device_topic AS d
      JOIN topics AS t ON t.id = d.topic_id
     WHERE t.is_private
    """
)

_DELETE_SQL = sa.text(
    """
    DELETE FROM device_topic AS d
     USING topics AS t
     WHERE t.id = d.topic_id
       AND t.is_private
    """
)


def release_private_room_pins(conn) -> dict[str, int]:
    """把私聊的机器绑定删掉，返回 {"before": …, "deleted": …, "after": …}。

    写成一个普通函数而不是塞进 ``upgrade``，是为了让测试跑的就是要发布的这段
    SQL，而不是照抄的一份。
    """
    before = conn.execute(_COUNT_SQL).scalar_one()
    deleted = conn.execute(_DELETE_SQL).rowcount
    after = conn.execute(_COUNT_SQL).scalar_one()
    return {"before": before, "deleted": deleted, "after": after}


def upgrade() -> None:
    result = release_private_room_pins(op.get_bind())
    logger.info("private_rooms_hold_no_machine: %s", result)
    print(f"private_rooms_hold_no_machine: {result}")


def downgrade() -> None:
    """不可逆，也不该逆：这些行记的是哪台机器被私聊占着，而私聊本来就不占机器。
    删掉的是一份谁也不再维护的旧声明，没有第二处可以把它算回来。No-op。"""
