"""device.visibility DDL default → isolated (#364)

#361 fixed only the OUTERMOST layer: the storage-agnostic `Device` dataclass in
`device/repository.py` defaults visibility to `isolated`. The two inner layers
kept the original `host` from #318 — the ORM `default=` and this column's DB
`server_default`. That is a defense-in-depth failure on the ACCESS axis: `host`
is not "glance at the box", it is a screen running `claude
--dangerously-skip-permissions` as the owner, able to read/write every other
room's worktree on that machine and exec into their containers (#358). The safe
reading of the access axis is the boxed room, and a forgotten value — a new
build-device path, a raw INSERT that skips the repository, a test fixture that
omits the field — must land there, not on whole-machine access. The failure
direction has to be toward least privilege, and it fails silent: nothing errors,
nothing reddens, until someone notices a device that can see the whole machine
with no trace of who forgot to pass the value.

The ORM `default` moves in `device/models.py`; this migration moves the DDL
`server_default` so the two agree.

**This ONLY changes the DDL default for FUTURE rows written without a value.** It
does NOT rewrite any existing row, on purpose. Existing devices were written
`host` by c4a71e5d9b30, and they are genuinely running bare — `isolated` has no
transport behind it yet (`resolve_pinned_device` would loudly reject it), so
flipping a live device to `isolated` would brick it. c4a71e5d9b30's backfill of
`server_default='host'` onto the pre-existing rows was correct and is left
untouched; this only decides what an omitted value becomes from here on.

The `supply` axis is deliberately left alone: `self_hosted` is the safe reading
of ITS axis (the destroy decision), so its default is already correct — the two
axes are safe in opposite directions and must not be flipped together.

Revision ID: 5fcb9dff4746
Revises: c4a71e5d9b30
Create Date: 2026-08-13 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5fcb9dff4746"
down_revision: str | Sequence[str] | None = "c4a71e5d9b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # DDL default only — no UPDATE, no backfill. Existing rows keep whatever they
    # were written (host for anything from c4a71e5d9b30); only omitted-value
    # INSERTs from here on get the access-safe `isolated`.
    op.alter_column("device", "visibility", server_default="isolated")


def downgrade() -> None:
    # Restore the pre-#364 DDL default. Row values are untouched either way.
    op.alter_column("device", "visibility", server_default="host")
