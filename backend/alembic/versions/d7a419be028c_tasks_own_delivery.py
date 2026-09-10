"""Move delivery ownership to tasks while retaining historical branches and records."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "d7a419be028c"
down_revision = "f31c07a9b45e"
branch_labels = None
depends_on = None


def preserve_room_files(workspace: Path, project: str, room: str) -> None:
    """Copy readable legacy files before room worktrees can be reclaimed.

    Run with room executors drained. The original tree remains the backup;
    interrupted copies resume in staging and become visible with one rename.
    """
    target = workspace / ".room-files" / project / room
    marker = target / ".imported-task-delivery"
    if marker.is_file():
        return
    if target.exists():
        raise RuntimeError(f"Room file destination already exists: {target}")
    tree_id = room.replace("-", "")
    pointer = workspace / ".place-trees" / tree_id
    if pointer.is_file():
        tree_id = pointer.read_text().strip().replace("-", "")
    source = workspace / ".worktrees" / project / f"topic_{tree_id[:8]}"
    if not source.is_dir():
        return
    if (source / ".git").exists():
        listing = subprocess.run(
            ["git", "-C", str(source), "ls-files", "-co", "--exclude-standard", "-z"],
            check=True,
            capture_output=True,
        )
    else:
        # Pre-Git rooms can still hold Jujutsu workspaces. An empty external
        # index applies their .gitignore rules without changing the old tree
        # or depending on a retired VCS binary. Never publish its .jj metadata.
        with tempfile.TemporaryDirectory(prefix="cheese-room-files-") as index:
            subprocess.run(
                ["git", "init", "--bare", index], check=True, capture_output=True
            )
            listing = subprocess.run(
                [
                    "git",
                    f"--git-dir={index}",
                    f"--work-tree={source}",
                    "ls-files",
                    "--others",
                    "--exclude-standard",
                    "--exclude=.jj",
                    "-z",
                ],
                check=True,
                capture_output=True,
            )
    paths = listing.stdout.decode().split("\0")
    staging = target.with_name(f".{room}.importing")
    staging.mkdir(parents=True, exist_ok=True)
    for relative in dict.fromkeys(paths):
        if not relative:
            continue
        original = source / relative
        if not original.is_file() or not original.resolve().is_relative_to(
            source.resolve()
        ):
            continue
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, destination)
    (staging / marker.name).write_text("d7a419be028c\n")
    staging.rename(target)


def upgrade() -> None:
    for name in ("branch_name", "workspace_name", "base_branch", "pr_url"):
        op.add_column("tasks", sa.Column(name, sa.String(255), nullable=True))
    op.add_column("tasks", sa.Column("pr_number", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("delivered_head", sa.String(64), nullable=True))
    op.add_column(
        "tasks", sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("tasks", sa.Column("last_check_ok", sa.Boolean(), nullable=True))
    op.add_column(
        "tasks",
        sa.Column("last_check_detail", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "tasks", sa.Column("historical_claimed_paths", sa.JSON(), nullable=True)
    )
    op.add_column("tasks", sa.Column("historical_delivery", sa.JSON(), nullable=True))
    for name in ("base_task_id", "historical_delivery_id"):
        op.add_column("tasks", sa.Column(name, sa.UUID(), nullable=True))
        op.create_foreign_key(
            f"fk_tasks_{name}", "tasks", "tasks", [name], ["id"], ondelete="SET NULL"
        )

    # Old tasks shared commits; assigning each a new PR would invent ownership.
    # Preserve their claims and links, and retain each old delivery as one task.
    op.execute("UPDATE tasks SET historical_claimed_paths = to_json(claimed_paths)")
    op.alter_column("tasks", "tree_id", nullable=True)
    op.execute("""
        INSERT INTO tasks (
            id, project_id, room_id, title, status, created_at, updated_at,
            branch_name, workspace_name, base_branch, pr_number, pr_url,
            delivered_head, last_check_at, last_check_ok, last_check_detail,
            accepted_at, closed_at, brief, contributor_handles, claimed_paths, historical_delivery
        )
        SELECT w.id, w.project_id, w.room_id,
            COALESCE((SELECT c.change_subject FROM accept_cards c WHERE c.tree_id = w.id
                      AND c.change_subject IS NOT NULL ORDER BY c.created_at DESC LIMIT 1), r.title),
            CASE WHEN w.status = 'merged' THEN 'closed' ELSE 'open' END,
            w.created_at, w.updated_at, 'topic/' || left(replace(w.id::text, '-', ''), 8),
            'topic_' || left(replace(w.id::text, '-', ''), 8), NULL, w.pr_number, w.pr_url,
            w.delivered_head, w.last_check_at, w.last_check_ok, w.last_check_detail,
            w.merged_at, w.merged_at, '', '[]', '{}', to_json(w)
        FROM work_trees w JOIN topics r ON r.id = w.room_id
    """)
    op.execute("""
        UPDATE tasks SET historical_delivery_id = tree_id,
            status = 'closed', closed_at = COALESCE(closed_at, now())
        WHERE tree_id IS NOT NULL
    """)
    op.execute("UPDATE accept_cards SET task_id = tree_id WHERE tree_id IS NOT NULL")
    op.execute("""
        UPDATE tasks t SET
            reviewer_handle = c.reviewer_handle,
            accepted_by = CASE WHEN t.accepted_at IS NOT NULL THEN c.decided_by END
        FROM (
            SELECT DISTINCT ON (tree_id) tree_id, reviewer_handle, decided_by
            FROM accept_cards WHERE tree_id IS NOT NULL
            ORDER BY tree_id, created_at DESC
        ) c WHERE t.id = c.tree_id
    """)
    # Unmapped terminal cards remain readable history. An active, unbound card
    # needs its actual PR branch identified before this migration can proceed.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM accept_cards c LEFT JOIN tasks t ON t.id = c.task_id
                WHERE c.status IN ('pending', 'pending_gate', 'conflict')
                  AND t.branch_name IS NULL
            ) THEN
                RAISE EXCEPTION 'Active acceptance cards lack a delivery branch; reconcile their PR heads before migrating';
            END IF;
        END $$
    """)
    op.drop_column("accept_cards", "tree_id")
    op.drop_column("tasks", "tree_id")
    op.drop_column("tasks", "claimed_paths")
    op.execute("DELETE FROM room_locks WHERE kind = 'file'")
    from app.core.config import settings

    for project, room in op.get_bind().execute(
        sa.text("SELECT project_id, id FROM topics")
    ):
        preserve_room_files(Path(settings.workspace_root), str(project), str(room))
    op.drop_table("work_trees")


def downgrade() -> None:
    raise RuntimeError(
        "Task deliveries cannot be collapsed into shared branches without losing ownership; restore a verified backup instead."
    )
