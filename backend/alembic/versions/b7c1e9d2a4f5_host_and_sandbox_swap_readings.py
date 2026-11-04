"""host_readings and sandbox_usages gain what swap and pressure show

A pool host now keeps a swap file (`cloud_host_swap_mb`), and the machines
page shows how much of it is used and how pressed its memory is. Each reading
also keeps memory pressure (PSI's `some`/`full` over a minute and five) and
whether zswap is loaded, and each sandbox's usage keeps its anonymous and file
pages, its swap and its OOM kills. Every one of them is optional: a machine
without the file or without the kernel's own reports leaves the value NULL.

The columns are nullable, so the release still serving while the migration
runs reads and writes the table as before.

Revision ID: b7c1e9d2a4f5
Revises: 7b3f1c8e5a02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "b7c1e9d2a4f5"
down_revision: str | Sequence[str] | None = "7b3f1c8e5a02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HOST = "host_readings"
SANDBOX = "sandbox_usages"


def upgrade() -> None:
    with_lock_retries("host_readings, sandbox_usages")
    op.add_column(HOST, sa.Column("swap_bytes", sa.BigInteger(), nullable=True))
    op.add_column(HOST, sa.Column("swap_free_bytes", sa.BigInteger(), nullable=True))
    op.add_column(
        HOST, sa.Column("memory_pressure_some_avg10", sa.Float(), nullable=True)
    )
    op.add_column(
        HOST, sa.Column("memory_pressure_some_avg60", sa.Float(), nullable=True)
    )
    op.add_column(
        HOST, sa.Column("memory_pressure_full_avg10", sa.Float(), nullable=True)
    )
    op.add_column(
        HOST, sa.Column("memory_pressure_full_avg60", sa.Float(), nullable=True)
    )
    op.add_column(HOST, sa.Column("zswap", sa.Boolean(), nullable=True))
    op.add_column(SANDBOX, sa.Column("anon_bytes", sa.BigInteger(), nullable=True))
    op.add_column(SANDBOX, sa.Column("file_bytes", sa.BigInteger(), nullable=True))
    op.add_column(SANDBOX, sa.Column("swap_bytes", sa.BigInteger(), nullable=True))
    op.add_column(SANDBOX, sa.Column("oom_kills", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with_lock_retries("host_readings, sandbox_usages")
    op.drop_column(SANDBOX, "oom_kills")
    op.drop_column(SANDBOX, "swap_bytes")
    op.drop_column(SANDBOX, "file_bytes")
    op.drop_column(SANDBOX, "anon_bytes")
    op.drop_column(HOST, "zswap")
    op.drop_column(HOST, "memory_pressure_full_avg60")
    op.drop_column(HOST, "memory_pressure_full_avg10")
    op.drop_column(HOST, "memory_pressure_some_avg60")
    op.drop_column(HOST, "memory_pressure_some_avg10")
    op.drop_column(HOST, "swap_free_bytes")
    op.drop_column(HOST, "swap_bytes")
