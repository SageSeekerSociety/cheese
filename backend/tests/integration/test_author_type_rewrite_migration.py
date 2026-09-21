"""两个旧作者档位的存量行改写成 participant（迁移 c1a7e05d4b83）—— 功能测试.

`blocks.author_type` 绑的是 Python 枚举（`Enum(AuthorType, native_enum=False)`），
所以「枚举里没有这个值」不是画错头像，是**一读就抛**：一条还写着 `human` 的行会
让任何取到它的查询 `LookupError`，整条时间线打不开。枚举删掉一档的那一次发布，
带着那一档的行必须跟着改。

反过来同样成立，这一份的第二个判据就是它：`system` 的行**不许动**。这一次发布只
把平台那一档在代码里改叫 `platform`，而迁移是在换容器之前跑的——窗口里服务的还是
上一版镜像，它的枚举里没有 `platform`。所以改写 `system` 的那一行 SQL 属于下一次
发布，这里出现它就是把 dev 在窗口里打穿。

测试跑的是**迁移里那段真实 SQL**（`rewrite_old_author_types`，从迁移模块导入），
不是照抄一份——不然测的就不是要发布的东西了。判据也落在读得出来的那一头：改写完
之后，同一批行由 ORM 取回来，每一条都是枚举认得的值。
"""

import asyncio
import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text

from app.domain.block.models import AuthorType, Block
from app.domain.project.models import Project
from app.domain.topic.models import Topic, TopicKind, TopicStatus

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "c1a7e05d4b83_the_two_old_author_values_leave_the_enum.py"
)

# 库里有过的每一个值，各来一行：上一版之前的人/芝士两档、平台那一档写下的名字，
# 上一版起就在写的 participant，以及这一版起写下的 platform。
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
    # 不动：改写它的是下一次发布，这一次动了就是让窗口里的上一版镜像读不了。
    "platform_under_its_old_name": AuthorType.system,
    "already_participant": AuthorType.participant,
    "new_platform": AuthorType.platform,
}


def _load_migration():
    spec = importlib.util.spec_from_file_location("_author_type_rewrite", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_author_type_row_comes_back_as_a_value_the_enum_has(client):
    """铺出库里有过的每一个值，跑一遍改写，再从 ORM 读回来核对。"""
    rewrite = _load_migration().rewrite_old_author_types
    ids: dict[str, uuid.UUID] = {}

    async def _seed() -> None:
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
                # `human`/`ai` 进不了 ORM——枚举里已经没有它们了，这正是要改写
                # 的理由；其余三个值照样从这里铺，同一条路，少一处差别。
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

    asyncio.run(_seed())

    async def _rewrite() -> None:
        async with client.test_factory() as s:
            await s.run_sync(lambda conn: rewrite(conn))
            await s.commit()

    asyncio.run(_rewrite())

    async def _read_back() -> tuple[dict[str, AuthorType], set[str]]:
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

    got, distinct = asyncio.run(_read_back())

    assert got == EXPECTED
    # 整张表只剩枚举认得的值——一条 human/ai 残留下来，下一次取到它就是 LookupError。
    assert distinct == {"participant", "platform", "system"}
