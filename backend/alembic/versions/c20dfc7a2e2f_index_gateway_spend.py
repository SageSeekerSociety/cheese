"""resource_usage: index a project's gateway spend for model admission

Revision ID: c20dfc7a2e2f
Revises: 4ce146cd5ebb

Every model request a session makes is admitted first (`POST /llm/admission`),
and the admission keeps the project's gateway key budget in step with its
grants: what the key has spent ever, plus what the project may still spend.
"What the key has spent ever" is `SUM(cost_usd)` over the project's
`route = 'gateway'` usage rows (`UsageRepository.gateway_spend`), read on every
admission. Through `ix_resource_usage_project_id` that visits every usage row of
the project and fetches each from the heap to test its route. Measured locally
on 240,000 rows (60,000 for the project, 20,000 of them gateway): 30 ms per
admission. On production `POST /llm/admission` ran 497 times in 38 minutes at
p50 144 ms.

A partial index on the gateway rows carrying `cost_usd` answers the sum from
the index alone: 3.3 ms on the same rows. The query writes the predicate as the
same literal (`app.domain.usage.models.GATEWAY_ROWS`) so the planner can prove
it for a prepared statement too.

Built CONCURRENTLY: `resource_usage` takes a row for every model call. An
invalid index left by a failed concurrent build is dropped before the retry.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c20dfc7a2e2f"
down_revision: str | Sequence[str] | None = "4ce146cd5ebb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = "ix_resource_usage_gateway_spend"
WHERE = "route = 'gateway'"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        valid = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT indisvalid FROM pg_index "
                    "WHERE indexrelid = to_regclass(:index)"
                ),
                {"index": NAME},
            )
            .scalar_one_or_none()
        )
        if valid is False:
            op.drop_index(NAME, postgresql_concurrently=True)
        op.create_index(
            NAME,
            "resource_usage",
            ["project_id"],
            postgresql_include=["cost_usd"],
            postgresql_where=sa.text(WHERE),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(NAME, postgresql_concurrently=True, if_exists=True)
