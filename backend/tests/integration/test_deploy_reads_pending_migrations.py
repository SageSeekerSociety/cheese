"""The deploy reads the pending migrations and the schema from a real database.

deploy/deploy-docker.sh feeds deploy/migration-touches.py to `python -` in the
release image before it migrates, and releases a standing connection owner when
the migrations change what the owner maps or the database already lacks it. A
wrong pending list, or a schema read that silently came back empty, would leave
an owner failing every query on a dropped column.
"""

import os
import subprocess
import sys
from pathlib import Path

from tests.integration.migration_replay import database_at

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND.parent / "deploy" / "migration-touches.py"


def test_a_database_before_a_drop_lists_it_and_what_it_drops() -> None:
    head = (BACKEND / "alembic" / "HEAD").read_text().strip()
    with database_at("c0ab1abfc178") as db:
        result = subprocess.run(
            [sys.executable, "-"],
            input=SCRIPT.read_text(),
            cwd=BACKEND,
            env={**os.environ, "DATABASE_URL": db.url},
            capture_output=True,
            text=True,
        )
    assert result.returncode == 0, result.stderr
    lines = [
        line.removeprefix("migration-touches ") for line in result.stdout.splitlines()
    ]
    assert lines[-1] == "ok"
    pending = {line.removeprefix("pending ") for line in lines if "pending " in line}
    # The drop of the external-problem tables and everything after it, up to head.
    assert {"6ac10b7f1b11", head} <= pending
    assert "c0ab1abfc178" not in pending
    dropped = {
        "external_problem_account",
        "external_problem_result",
        "task_external_problem",
    }
    assert dropped <= set(lines)
    present = {line.removeprefix("present ") for line in lines if "present " in line}
    # Not dropped yet: the database still has them.
    assert {"external_problem_account", "topics", "topics.id"} <= present
