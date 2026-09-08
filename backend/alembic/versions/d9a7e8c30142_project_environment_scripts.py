"""Replace project image selection with scripts and pin existing rooms.

Revision ID: d9a7e8c30142
Revises: c2d7e9f1a718
"""

import hashlib
import json
import shlex

import sqlalchemy as sa

from alembic import op

revision = "d9a7e8c30142"
down_revision = "c2d7e9f1a718"
branch_labels = None
depends_on = None

# This replaces the toolchain the retired image supplied, in the room's HOME.
SETUP = r"""set -euo pipefail
mkdir -p "$HOME/.local/bin"
if [ "$(uv --version 2>/dev/null || true)" != "uv 0.12.10" ]; then
  curl -fsSL https://astral.sh/uv/0.12.10/install.sh -o "$HOME/.local/uv-install.sh"
  UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh "$HOME/.local/uv-install.sh"
fi
if [ "$(node --version 2>/dev/null || true)" != "v22.14.0" ]; then
  case "$(uname -s)" in Darwin) platform=darwin;; Linux) platform=linux;; *) exit 1;; esac
  case "$(uname -m)" in arm64|aarch64) arch=arm64;; x86_64) arch=x64;; *) exit 1;; esac
  archive="node-v22.14.0-$platform-$arch.tar.gz"
  curl -fsSL "https://nodejs.org/dist/v22.14.0/$archive" -o "$HOME/.local/$archive"
  tar -xzf "$HOME/.local/$archive" -C "$HOME/.local"
  for tool in node npm npx corepack; do
    ln -sf "$HOME/.local/node-v22.14.0-$platform-$arch/bin/$tool" "$HOME/.local/bin/$tool"
  done
fi
if [ "$(pnpm --version 2>/dev/null || true)" != "9.15.3" ]; then
  npm install --global --prefix "$HOME/.local" pnpm@9.15.3
fi
"""
STARTUP = "(cd backend && uv sync --frozen)\n(cd frontend && pnpm install --frozen-lockfile)\n"


def upgrade() -> None:
    op.add_column("topics", sa.Column("environment", sa.JSON(), nullable=True))
    connection = op.get_bind()
    projects = sa.table(
        "projects", sa.column("id", sa.Uuid()), sa.column("settings", sa.JSON())
    )
    topics = sa.table(
        "topics",
        sa.column("project_id", sa.Uuid()),
        sa.column("environment", sa.JSON()),
    )
    for row in connection.execute(
        sa.select(projects.c.id, projects.c.settings)
    ).mappings():
        settings = dict(row["settings"] or {})
        image = settings.pop("sandbox_image", None)
        config = {"setup_script": "", "startup_script": "", "variables": {}}
        if image == "cheesex-dev:v0":
            config.update(setup_script=SETUP, startup_script=STARTUP)
        elif image:
            # An arbitrary image's contents cannot be inferred from its name.
            message = shlex.quote(
                f"Replace the retired custom image {image} with setup scripts in project settings."
            )
            config["setup_script"] = f"printf '%s\\n' {message} >&2\nexit 1\n"
        config["revision"] = hashlib.sha256(
            json.dumps(config, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()
        settings["environment"] = config
        connection.execute(
            projects.update()
            .where(projects.c.id == row["id"])
            .values(settings=settings)
        )
        connection.execute(
            topics.update()
            .where(topics.c.project_id == row["id"])
            .values(environment=config)
        )


def downgrade() -> None:
    # Script configurations remain in project settings; rollback does not erase them.
    op.drop_column("topics", "environment")
