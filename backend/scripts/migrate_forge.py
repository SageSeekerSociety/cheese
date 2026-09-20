"""Copy legacy local repositories into Forgejo after stopping repository writers.

Run from backend: python -m scripts.migrate_forge --backup-root PATH
Add --apply --writers-stopped after quiescing the deployment.
"""

import argparse
import asyncio
import json
import logging
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.db import async_session_factory
from app.domain.project.forge import (
    binding_for_project,
    provision_repository,
    tokens_for_project,
)
from app.domain.project.forge_migration import (
    freeze,
    push,
    task_snapshot,
    unpack_working_files,
    write_checkpoint,
)
from app.domain.project.models import Project
from app.domain.room_task import snapshots
from app.domain.room_task.models import Task


async def migrate(project_id: uuid.UUID, backups: Path, *, apply: bool) -> str:
    log = logging.getLogger("forge-migration")
    log.info("project=%s status=start apply=%s", project_id, apply)
    async with async_session_factory() as session:
        binding = await binding_for_project(project_id, session)
        project = await session.get(Project, project_id)
        if (
            binding is None
            and project
            and (project.settings or {}).get("forge_kind") == "github_app"
        ):
            log.info("project=%s status=skip reason=awaiting_github", project_id)
            return "skipped"
        source = Path(settings.workspace_root).resolve() / str(project_id)
        if binding is not None and binding.kind == "github_app" and not source.exists():
            log.info("project=%s status=skip reason=no_local_repository", project_id)
            return "skipped"
        project_backup = backups / str(project_id)
        completed = project_backup / "completed.json"
        if completed.exists():
            receipt = json.loads(completed.read_text())
            if binding is None or receipt["repo"] != binding.repo:
                raise ValueError(
                    "Completed migration does not match the project binding"
                )
            log.info("project=%s status=skip reason=completed", project_id)
            return "skipped"
        if not apply:
            log.info(
                "project=%s status=planned local_repository=%s",
                project_id,
                source.exists(),
            )
            return "planned"
        checkpoint = None
        if source.exists():
            candidates = sorted(project_backup.glob("*/source.json"))
            output = (
                candidates[-1].parent
                if candidates
                else project_backup / ("attempt-" + uuid.uuid4().hex)
            )
            checkpoint = await asyncio.to_thread(
                freeze, source.parent, project_id, output
            )
            log.info("project=%s status=backed_up checkpoint=%s", project_id, output)
        binding = await provision_repository(
            project_id, session, initialize=not source.exists()
        )
        if checkpoint is not None:
            if binding.kind == "forgejo":
                tokens = await tokens_for_project(project_id, session)
                token, _ = await tokens.installation_token()
                url = (
                    binding.api_url.removesuffix("/api/v1").rstrip("/")
                    + f"/{binding.repo}.git"
                )
                refs = await asyncio.to_thread(push, output, url, token)
                if refs:
                    async with httpx.AsyncClient(timeout=30) as client:
                        response = await client.patch(
                            f"{binding.api_url}/repos/{binding.repo}",
                            headers={"Authorization": "token " + token},
                            json={"default_branch": checkpoint["default_branch"]},
                        )
                        response.raise_for_status()
                    binding.default_branch = checkpoint["default_branch"]
                log.info(
                    "project=%s status=refs_verified count=%s", project_id, len(refs)
                )
            else:
                # GitHub remains authoritative. Local-only history stays in the
                # immutable archive and self-contained private task bundles.
                log.info("project=%s status=local_refs_archived", project_id)
            tasks = list(
                await session.scalars(select(Task).where(Task.project_id == project_id))
            )
            with tempfile.TemporaryDirectory(
                dir=output, prefix="working-"
            ) as working_files:
                if any(task.workspace_name and task.branch_name for task in tasks):
                    log.info("project=%s status=unpack_start", project_id)
                    await asyncio.to_thread(
                        unpack_working_files, output, Path(working_files)
                    )
                    log.info("project=%s status=unpack_complete", project_id)
                for task in tasks:
                    if not task.workspace_name or not task.branch_name:
                        log.info("task=%s status=skip reason=no_workspace", task.id)
                        continue
                    log.info("task=%s status=snapshot_start", task.id)
                    backup = await asyncio.to_thread(
                        task_snapshot,
                        output,
                        task.id,
                        working_files=Path(working_files),
                        directory=task.workspace_name,
                        branch=task.branch_name,
                        include_history=binding.kind == "github_app",
                    )
                    if backup is None:
                        log.info("task=%s status=skip reason=no_local_changes", task.id)
                        continue
                    with (output / backup["file"]).open("rb") as stream:
                        await snapshots.save(
                            session,
                            task,
                            file=stream,
                            head_sha=backup["head_sha"],
                            snapshot_sha=backup["snapshot_sha"],
                            digest=backup["digest"],
                        )
                    # A later upload failure must not roll back completed task backups.
                    await session.commit()
                    log.info(
                        "task=%s status=snapshot_saved digest=%s",
                        task.id,
                        backup["digest"],
                    )
        await session.commit()
        project_backup.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_checkpoint(
            completed,
            {
                "project_id": str(project_id),
                "repo": binding.repo,
                "source_checkpoint": str(output) if checkpoint else None,
                "completed_at": datetime.now(UTC).isoformat(),
            },
        )
        log.info("project=%s status=complete", project_id)
        return "migrated"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--project", type=uuid.UUID)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument(
        "--check",
        action="store_true",
        help="Exit 2 when repository migration is pending",
    )
    parser.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args()
    if args.apply and not args.writers_stopped:
        parser.error("Stop repository writers before using --apply --writers-stopped")
    backups = args.backup_root.resolve()
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    logging.basicConfig(
        filename=backups / "migration.log",
        level=logging.INFO,
        format="%(asctime)s %(message)s",
    )
    async with async_session_factory() as session:
        query = select(Project.id).order_by(Project.id)
        if args.project:
            query = query.where(Project.id == args.project)
        projects = list(await session.scalars(query))
    counts = {"migrated": 0, "skipped": 0, "planned": 0, "failed": 0}
    for project_id in projects:
        try:
            result = await migrate(project_id, backups, apply=args.apply)
            counts[result] += 1
        except Exception:
            logging.getLogger("forge-migration").exception(
                "project=%s status=failed", project_id
            )
            counts["failed"] += 1
    print(json.dumps(counts))
    if counts["failed"]:
        raise SystemExit(1)
    if args.check and counts["planned"]:
        raise SystemExit(2)


if __name__ == "__main__":
    asyncio.run(main())
