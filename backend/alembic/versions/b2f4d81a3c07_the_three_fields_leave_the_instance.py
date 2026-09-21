"""the model, the harness and the effort leave a saved configuration

Revision ID: b2f4d81a3c07
Revises: c5e7d2a91f30
Create Date: 2026-09-21 21:00:00

``agent_instances.configuration`` has carried six keys since d7a91c4e2b60 wrote
them: the three that say what an agent IS — ``body``, ``skills``,
``mcp_servers`` — and the three that said what it RUNS ON. The last three moved
out of the code one release ago (#1375): the model is bound to a piece of work
(``room_task/binding.py``, #1365) and the harness is a deployment setting. Since
that release nothing reads them and nothing writes them, so what is left in the
rows is a second, silent declaration of「用哪个模型」 — the kind two-places-to-set
problem where nobody can say which one wins, because neither one does anything.

This clears them out, and the schema they hung off is deleted in the same
commit. The two halves have to be one release apart, not one commit: from this
migration finishing to the last container being replaced, the image serving
requests is the previous one, and it must still build an ``AgentConfiguration``
out of a row that no longer has the keys. It can — that release already made
them optional and stopped reading them — which is what this migration was
waiting for.

The update is unconditional rather than guarded by「这一行还带着键吗」: the row
count here is the number of agents, so rewriting them all costs less than the
second pass a WHERE clause would need to decide, and it leaves nothing to get
the condition wrong about.

Downgrade puts the keys back only as far as an empty configuration can be said
to have had them — which is not at all — so it does not pretend to.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b2f4d81a3c07"
down_revision: str | Sequence[str] | None = "c5e7d2a91f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The column is `json`, which has no key-removal operator of its own; the
    # cast to `jsonb` and back is what `-` needs, and it also normalizes the
    # whitespace the old writer left in.
    op.execute(
        "UPDATE agent_instances SET configuration = "
        "((configuration)::jsonb - 'model' - 'harness' - 'effort')::json"
    )


def downgrade() -> None:
    """The values are gone; a downgrade has nothing to put back."""
