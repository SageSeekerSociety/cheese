"""A GitHub repo belongs to one project; an installation may serve many

``project_git_installations.installation_id`` was unique, which let only one
project per GitHub org use the App: an org installs it once and that single
installation covers every repo it granted, so a second project connecting a
different repo in the same org was refused. The reason given was that a token
minted for a shared installation would be ambiguous about which project it
serves — no longer true, since every token is minted for the one repo its
project is bound to.

What must stay unique is the repo itself, so the constraint moves there. The
existing rows already satisfy it: until now a repo could only be bound once,
because its installation could.

Revision ID: c5e8a1f47b20
Revises: 6b979dd23cd9
Create Date: 2026-09-28 16:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c5e8a1f47b20"
down_revision: str | Sequence[str] | None = "6b979dd23cd9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_project_git_installations_installation",
        "project_git_installations",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_project_git_installations_repo", "project_git_installations", ["repo"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_project_git_installations_repo",
        "project_git_installations",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_project_git_installations_installation",
        "project_git_installations",
        ["installation_id"],
    )
