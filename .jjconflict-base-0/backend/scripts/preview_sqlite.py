"""Preview bootstrap (no Postgres): sqlite schema + demo seed + 文件树演示数据.

For sandbox preview of the frontend. Creates the schema with create_all (the
sqlite file is fresh, so seed_demo's Postgres-only TRUNCATE is patched to a
no-op), runs the standard demo seed, then adds what the 「文件跳转自动展开路径」
change needs to be seen:
- deeply nested files in the demo topic's worktree;
- a chat message + doc section containing <&path> file chips to click.

Run: cd backend && DATABASE_URL=sqlite+aiosqlite:////work/backend/preview.db \
     PYTHONPATH=. uv run python scripts/preview_sqlite.py
"""

import asyncio

from sqlalchemy import select

import scripts.seed_demo as seed_demo
from app.core.db import Base, async_session_factory, engine
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.topic.models import Topic
from app.domain.workspace import service as ws


async def _reset_noop(_session) -> None:  # fresh sqlite file — nothing to reset
    return None


seed_demo._reset = _reset_noop

_FILES = {
    "backend/app/domain/recommend/cf_model.py": (
        '"""Item-based CF 原型（演示文件）."""\n\n\ndef similar_items(matrix):\n'
        "    ...\n"
    ),
    "backend/app/domain/recommend/data_loader.py": (
        '"""教务处脱敏数据加载（演示文件）."""\n'
    ),
    "backend/eval/metrics/recall.py": (
        '"""Recall@K 评测指标（演示文件）."""\n\n\ndef recall_at_k(pred, truth, k=10):\n'  # noqa: E501
        "    ...\n"
    ),
    "backend/eval/run_eval.py": '"""离线评测入口（演示文件）."""\n',
    "README.md": "# 推荐算法原型\n\n演示项目。\n",
}


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_demo.seed()

    async with async_session_factory() as s:
        topic = (
            await s.execute(select(Topic).where(Topic.title == "搭建推荐算法原型"))
        ).scalar_one()
        pid, tid = topic.project_id, topic.id
        for path, content in _FILES.items():
            ws.write_file(pid, path, content, tid)
        s.add(
            Block(
                project_id=pid,
                topic_id=tid,
                kind=BlockKind.message,
                author_type=AuthorType.ai,
                author="cheese",
                content=(
                    "算法骨架写好了：核心相似度在 <&backend/app/domain/recommend/cf_model.py>，"  # noqa: E501
                    "评测指标在 <&backend/eval/metrics/recall.py>，"
                    "入口是 <&backend/eval/run_eval.py>。点开看看。"
                ),
            )
        )
        await s.commit()
    print(f"preview seed OK: project={pid} topic={tid}")


if __name__ == "__main__":
    asyncio.run(main())
