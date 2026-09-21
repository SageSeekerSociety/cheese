"""退役的作者档位，存量行跟着改写（迁移 c1a7e05d4b83 与 2895c4967ca4）—— 功能测试.

`blocks.author_type` 绑的是 Python 枚举（`Enum(AuthorType, native_enum=False)`），
所以「枚举里没有这个值」不是画错头像，是**一读就抛**：一条还写着 `human` 的行会
让任何取到它的查询 `LookupError`，整条时间线打不开。枚举删掉一档的那一次发布，
带着那一档的行必须跟着改。

三个旧名字分两次离场，因为部署换容器有先后（`deploy/deploy-docker.sh` 先跑
`alembic upgrade head`，再换镜像）：一次发布只能做上一版镜像读得懂的数据改动。
`c1a7e05d4b83` 改写 `human`/`ai`，`2895c4967ca4` 改写平台那一档的旧名字 `system`。
两条一起跑完，库里才只剩枚举认得的值——所以这一份把它们当作**一条链**来跑，判据
落在读得出来的那一头：改写完之后，同一批行由 ORM 取回来，每一条都是枚举认得的值。

测试跑的是**迁移的 `upgrade()` 本身**——把迁移模块加载进来，配一个真的 alembic
operations 上下文，然后调它，跟 `alembic upgrade head` 走的是同一条路。不抽一个
函数出来绕开 alembic：那样一来「`upgrade()` 确实调了改写」就没人盯着，把那一行
删掉这条测试照样全绿，而 dev 部署之后每一条旧行被取到都是 `LookupError`。
"""

import asyncio
import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, select, text

from app.domain.block.models import AuthorType, Block
from app.domain.project.models import Project
from app.domain.topic.models import Topic, TopicKind, TopicStatus

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"

# 链上的两条，按 alembic 的先后。
_TWO_OLD_VALUES = _VERSIONS / "c1a7e05d4b83_the_two_old_author_values_leave_the_enum.py"
_SYSTEM_LEAVES = _VERSIONS / "2895c4967ca4_system_leaves_the_enum.py"

# 库里有过的、以及往后会有的每一个值，各来一行：上一版之前的人/芝士两档、平台那
# 一档留在存量行上的旧名字，以及今天的写入端写下的 participant 和 platform。
SEEDED = {
    "old_person": "human",
    "old_agent": "ai",
    "platform_under_its_old_name": "system",
    "already_participant": "participant",
    "new_platform": "platform",
}

EXPECTED = {
    "old_person": AuthorType.participant,
    "old_agent": AuthorType.participant,
    "platform_under_its_old_name": AuthorType.platform,
    "already_participant": AuthorType.participant,
    "new_platform": AuthorType.platform,
}


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"_mig_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(client) -> dict[str, uuid.UUID]:
    """库里有过的每一个值各铺一行，返回每一行的 id。"""
    ids: dict[str, uuid.UUID] = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            project = Project(name="P", owner_handle="alice")
            s.add(project)
            await s.flush()
            topic = Topic(
                project_id=project.id,
                title="房间",
                kind=TopicKind.topic,
                status=TopicStatus.active,
            )
            s.add(topic)
            await s.flush()

            now = datetime.now(UTC)
            for key, stored in SEEDED.items():
                block_id = uuid.uuid4()
                ids[key] = block_id
                # 退役的值进不了 ORM——枚举里已经没有它们了，这正是要改写的理由；
                # 其余两个值照样从这里铺，同一条路，少一处差别。
                await s.execute(
                    text(
                        "INSERT INTO blocks (id, project_id, topic_id, kind, "
                        "author_type, author, content, refs, created_at, "
                        "updated_at) VALUES (:id, :pid, :tid, 'message', :at, "
                        ":author, :content, '[]', :now, :now)"
                    ),
                    {
                        "id": block_id,
                        "pid": project.id,
                        "tid": topic.id,
                        "at": stored,
                        "author": "alice" if stored != "ai" else "cheese",
                        "content": key,
                        "now": now,
                    },
                )
            await s.commit()

    asyncio.run(_run())
    return ids


def _upgrade(client, modules) -> None:
    """按给定顺序把每条迁移的 `upgrade()` 跑一遍。"""

    def _apply(conn) -> None:
        """跟 `alembic upgrade head` 一样：给 `op` 配上下文，再调 `upgrade()`。"""
        for module in modules:
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()

    async def _run() -> None:
        async with client.test_factory() as s:
            # 迁移拿到的必须是一条 Connection（`op` 要从它读 dialect），
            # 而 `AsyncSession.run_sync` 递过来的是 Session。
            await (await s.connection()).run_sync(_apply)
            await s.commit()

    asyncio.run(_run())


def _upgrade_counting_touched_rows(client, module) -> int:
    """再跑一遍这条迁移，返回它的 `UPDATE` 一共匹配到几行。

    改写过的行数只有数据库自己数得出来——语句跑完表是什么样，看不出它这一遍碰过
    谁。所以挂在连接上收 `cursor.rowcount`，那是 `UPDATE` 的 `WHERE` 匹配到的行数。
    """
    touched: list[int] = []

    def _apply(conn) -> None:
        def _record(conn_, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("UPDATE"):
                touched.append(cursor.rowcount)

        event.listen(conn, "after_cursor_execute", _record)
        try:
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        finally:
            event.remove(conn, "after_cursor_execute", _record)

    async def _run() -> None:
        async with client.test_factory() as s:
            await (await s.connection()).run_sync(_apply)
            await s.commit()

    asyncio.run(_run())
    assert touched, "迁移没有跑出 UPDATE，这条测试就什么也没核到"
    return sum(touched)


def _read_back(client, ids) -> tuple[dict[str, AuthorType], set[str]]:
    async def _run() -> tuple[dict[str, AuthorType], set[str]]:
        async with client.test_factory() as s:
            got = {
                key: (await s.execute(select(Block).where(Block.id == bid)))
                .scalar_one()
                .author_type
                for key, bid in ids.items()
            }
            distinct = {
                row[0]
                for row in (
                    await s.execute(text("SELECT DISTINCT author_type FROM blocks"))
                ).all()
            }
            return got, distinct

    return asyncio.run(_run())


def test_every_author_type_row_comes_back_as_a_value_the_enum_has(client):
    """铺出库里有过的每一个值，把链跑一遍，再从 ORM 读回来核对。"""
    ids = _seed(client)
    _upgrade(client, [_load(_TWO_OLD_VALUES), _load(_SYSTEM_LEAVES)])

    got, distinct = _read_back(client, ids)

    assert got == EXPECTED
    # 整张表只剩枚举认得的值——一条残留下来，下一次取到它就是 LookupError。
    assert distinct == {"participant", "platform"}


def test_the_second_run_of_the_system_rewrite_touches_no_rows(client):
    """重跑是空操作：第二遍的 `UPDATE` 一行都不碰。

    迁移在换容器之前跑，而部署会重来（回滚再上、一次失败的 deploy 重试）。迁移文件
    说这不要紧，理由是 `WHERE` 点名的是 `system` 这个退役的值，已经改过的行第二遍
    不再匹配——这条测试就是那句话的判据。

    数据本身核不出这个差别：第二遍之后表里是什么样，第一遍之后就已经是什么样了。
    一条写成「不是 participant 的都改」的 `WHERE` 两遍下来数据也照样对，它跟点名
    `system` 的写法唯一读得出来的差别，是第二遍还匹配着那些已经改完的行。所以这里
    看的是第二遍 `UPDATE` 的 rowcount。
    """
    _seed(client)
    system_leaves = _load(_SYSTEM_LEAVES)
    _upgrade(client, [_load(_TWO_OLD_VALUES), system_leaves])

    assert _upgrade_counting_touched_rows(client, system_leaves) == 0
