"""存量行的三个旧作者档位改写成两档（迁移 c1a7e05d4b83）—— 功能测试.

`blocks.author_type` 绑的是 Python 枚举（`Enum(AuthorType, native_enum=False)`），
所以「枚举里没有这个值」不是画错头像，是**一读就抛**：一条还写着 `human` 的行会
让任何取到它的查询 `LookupError`，整条时间线打不开。枚举改形状的那一次发布，行
必须跟着改。

测试跑的是**迁移里那段真实 SQL**（`rewrite_old_author_types`，从迁移模块导入），
不是照抄一份——不然测的就不是要发布的东西了。判据也落在读得出来的那一头：改写完
之后，同一批行由 ORM 取回来，每一条都是今天枚举里的一档。
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
    / "c1a7e05d4b83_author_type_keeps_only_participant_and_platform.py"
)

# 库里有过的每一个值，各来一行：P8 之前的人/芝士两档、平台那一档的旧名字，以及
# P8 之后已经在写的 participant。
SEEDED = {
    "old_person": "human",
    "old_agent": "ai",
    "old_platform": "system",
    "already_participant": "participant",
}

EXPECTED = {
    "old_person": AuthorType.participant,
    "old_agent": AuthorType.participant,
    "old_platform": AuthorType.platform,
    "already_participant": AuthorType.participant,
}


def _load_migration():
    spec = importlib.util.spec_from_file_location("_author_type_rewrite", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_old_author_type_row_comes_back_as_one_of_the_two(client):
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
                # 旧值进不了 ORM——枚举里已经没有它们了，这正是要改写的理由。
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
    # 整张表只剩枚举认得的两个值——一条旧行残留下来，下一次取到它就是 LookupError。
    assert distinct == {"participant", "platform"}
