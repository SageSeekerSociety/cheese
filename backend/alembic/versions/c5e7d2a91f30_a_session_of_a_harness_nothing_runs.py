"""a session row of a harness nothing will run again leaves

Revision ID: c5e7d2a91f30
Revises: d5c48f1a6b73
Create Date: 2026-09-21 16:00:00

``agent_sessions`` is keyed by ``(topic, agent_handle, harness)``. Until this
release the harness half came off the instance — an open dropdown that offered
Claude Code, Codex and pi — and from this release on every read and every write
uses the one harness this deployment runs. Rows stored under the other two are
therefore no longer addressed by anything: nothing will ever write them again,
and nothing will ever look them up again. dev holds 21 of them (1 codex, 20 pi),
all room rows, none sharing a ``(topic, agent_handle)`` with a claude-code row.

Left alone they do not sit quietly. ``runtime_location`` is not cleared when a
session stops, so ``placed_in_room`` and ``placed_everywhere`` keep handing them
out: ``harness_in_room`` answers "pi" for a room whose turns now run Claude Code,
which tells the terminal endpoint the pane will stay black, and a cold start has
the pi channel re-adopt a screen nobody is working in.

They are DELETED rather than folded onto ``claude-code``, because what a folded
row carries is wrong twice over. ``resume_token`` is the other harness's own
session id and Claude Code cannot resume it — it would be handed a token it
cannot use, which is worse than a cold start. ``runtime_location`` names a screen
running a pi or codex runner, and ``central_provider.discover`` skips those rows
today only because their harness is not ``claude-code``; rename the harness and
the Claude Code channel claims the screen and finds nothing of its own on it.

Deleting a row here is not the 不可再生的行 the plan protects: a session row is
machinery, rebuilt by the next turn, not content a person or an agent wrote with
no second copy. What the room loses is the conversation of a harness that will
not run in it again, and the state it lands in — no session row — is exactly the
state of a room that has never run Claude Code, which is the truth about it.

The delete is also correct while the previous image is still serving in the
container-swap window: that image would resolve such a room to pi again, so the
worst the window holds is one pi session starting afresh instead of resuming.

Downgrade cannot restore them and does not pretend to.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c5e7d2a91f30"
down_revision: str | Sequence[str] | None = "d5c48f1a6b73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A row of the one harness this deployment runs is kept whatever else is
    # under the same key: on the (empty on dev, possible elsewhere) collision
    # the claude-code row is the one anything still addresses.
    op.execute("DELETE FROM agent_sessions WHERE harness <> 'claude-code'")


def downgrade() -> None:
    """The rows are gone; a downgrade has nothing to put back."""
