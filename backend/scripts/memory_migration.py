"""把一个项目的旧记忆（`memory_entries`）搬进文件树：dry-run / approve / apply。

        uv run python -m scripts.memory_migration --project <id 或名字的一部分>
        uv run python -m scripts.memory_migration --project cheese --report-out r.md
        uv run python -m scripts.memory_migration --plan <plan id> --approve
        uv run python -m scripts.memory_migration --plan <plan id> --apply

三件事按顺序发生，中间那一步**必须是人**：

1. `dry-run`（默认）读这个项目的旧记忆（两个池 + 总览文档里那两节）、问一遍模型、
   算出一份计划，把**报告**打到 stdout（`--report-out` 也能存成文件）。这一步一个
   字都不写进新树。
2. `--approve` 由复核人执行（`settings.memory_migration_reviewer`，默认
   `wangchangxin`）。别人执行会被拒——这是这次迁移唯一的闸。approve 之后报告的内
   容不会再变（apply 重放它，不再问模型）。
3. `--apply` 按那份计划写进 `memory_files`。**一次事务**：一条版本冲突就整次不写，
   留下「什么都没发生」，而不是半棵树。

`--project` 认 id，也认名字的一部分（大小写不敏感）——试点要在「cheese 自建」上先
跑，而没有人会去背那个 uuid。名字撞上多个就全列出来让你选，不猜。

**旧表在这条路上只读。** 搬完不删、不改、不标记；删表是另一个迁移，等搬完看一阵
（30 天）再说。
"""

import argparse
import asyncio
import sys
import uuid

from sqlalchemy import select

from app.core.config import settings
from app.core.db import async_session_factory
from app.domain.memory.migration_service import MemoryMigrationService
from app.domain.memory.models import MemoryMigrationPlan, MemoryMigrationStatus
from app.domain.project.models import Project


async def _resolve_project(session, wanted: str) -> uuid.UUID:
    """项目 id，或者名字的一部分。撞上多个就列出来、退出——不猜。"""
    try:
        project_id = uuid.UUID(wanted)
    except ValueError:
        pass
    else:
        if await session.get(Project, project_id) is not None:
            return project_id
        raise SystemExit(f"没有这个项目：{project_id}")
    rows = list(
        (
            await session.scalars(
                select(Project).where(Project.name.ilike(f"%{wanted}%"))
            )
        ).all()
    )
    if not rows:
        raise SystemExit(f"没有名字里带 {wanted!r} 的项目")
    if len(rows) > 1:
        print(f"{wanted!r} 对上了 {len(rows)} 个项目，换一个更准的：")
        for row in rows:
            print(f"  {row.id}  {row.name}")
        raise SystemExit(1)
    print(f"项目：{rows[0].name} ({rows[0].id})\n")
    return rows[0].id


async def _load(session, plan_id: str) -> MemoryMigrationPlan:
    try:
        parsed = uuid.UUID(plan_id)
    except ValueError:
        raise SystemExit(f"这不是一个计划 id：{plan_id}") from None
    row = await session.get(MemoryMigrationPlan, parsed)
    if row is None:
        raise SystemExit(f"没有这个计划：{parsed}")
    return row


async def _dry_run(project: str, report_out: str | None) -> int:
    async with async_session_factory() as session:
        project_id = await _resolve_project(session, project)
        service = MemoryMigrationService(session)
        print("读旧记忆、问模型…（这一步可能要几分钟）\n")
        row = await service.dry_run(project_id, by="script")
        await session.commit()
        print(row.report)
        if report_out:
            with open(report_out, "w", encoding="utf-8") as handle:
                handle.write(row.report)
            print(f"\n报告也写到 {report_out}")
        print(
            f"\n计划 {row.id}（{row.status}）。复核人："
            f"{settings.memory_migration_reviewer}\n"
            f"  复核：uv run python -m scripts.memory_migration --plan {row.id}"
            " --approve\n"
            f"  落笔：uv run python -m scripts.memory_migration --plan {row.id} --apply"
        )
        return 0


async def _approve(plan_id: str, by: str) -> int:
    async with async_session_factory() as session:
        row = await _load(session, plan_id)
        await MemoryMigrationService(session).approve(row.id, by=by)
        await session.commit()
        print(f"计划 {row.id} 已复核（{by}）。")
        return 0


async def _apply(plan_id: str) -> int:
    async with async_session_factory() as session:
        row = await _load(session, plan_id)
        service = MemoryMigrationService(session)
        applied = await service.apply(row.id, by="script")
        await session.commit()
        print(applied.summary)
        print(
            f"\n计划 {applied.id}：{applied.status}。旧表一个字都没动"
            "（删表是另一个迁移，30 天后再谈）。"
        )
        return 0


async def _list_plans(project: str) -> int:
    async with async_session_factory() as session:
        project_id = await _resolve_project(session, project)
        rows = await MemoryMigrationService(session).plans_of(project_id)
        if not rows:
            print("这个项目还没有搬过。")
            return 0
        for row in rows:
            print(
                f"{row.id}  {row.status:8}  {row.created_at:%Y-%m-%d %H:%M}  "
                f"{len(row.source_ids):4} 条 → {len(row.files)} 个文件  "
                f"复核={row.approved_by or '-'}  {row.summary}"
            )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="项目 id，或名字的一部分（dry-run / --list）")
    parser.add_argument("--plan", help="计划 id（--approve / --apply / --show）")
    parser.add_argument("--report-out", help="把报告整份写进这个文件（dry-run）")
    parser.add_argument("--list", action="store_true", help="列出这个项目搬过的几次")
    parser.add_argument("--show", action="store_true", help="把一份报告打出来")
    parser.add_argument("--approve", action="store_true", help="复核（只认复核人）")
    parser.add_argument(
        "--by", default=settings.memory_migration_reviewer, help="复核人的 handle"
    )
    parser.add_argument("--apply", action="store_true", help="落笔")
    args = parser.parse_args()
    if args.approve and args.apply:
        raise SystemExit("--approve 和 --apply 是两步，分开跑")
    if args.plan:
        if args.approve:
            return asyncio.run(_approve(args.plan, args.by))
        if args.apply:
            return asyncio.run(_apply(args.plan))
        if args.show:
            return asyncio.run(_show(args.plan))
        raise SystemExit("给了 --plan 就要说做什么：--approve / --apply / --show")
    if not args.project:
        raise SystemExit("要么给 --project（dry-run / --list），要么给 --plan")
    if args.list:
        return asyncio.run(_list_plans(args.project))
    return asyncio.run(_dry_run(args.project, args.report_out))


async def _show(plan_id: str) -> int:
    async with async_session_factory() as session:
        row = await _load(session, plan_id)
        print(row.report)
        print(
            f"\n[{row.status}] 复核={row.approved_by or '-'} "
            f"落笔={row.applied_at or '-'} {row.summary}"
        )
        if row.status == MemoryMigrationStatus.draft.value:
            print(
                f"（等复核：--approve，复核人是 {settings.memory_migration_reviewer}）"
            )
        return 0


if __name__ == "__main__":
    sys.exit(main())
