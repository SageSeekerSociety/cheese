"""A task's video moves into its description

A task's video link had a column of its own and was shown under the
description. The description's editor now shows a Bilibili link standing on a
line of its own as the player, so each task's link becomes the first block of
its description and the column goes.

A description is either the editor's JSON document or, for a task imported
from a PDF, Markdown. A Bilibili link becomes the editor's ``video`` node (its
name and attribute are the editor's own; ``richTextVideo.spec.ts`` holds the
editor to them), any other link a paragraph holding that link. In Markdown the
link is written as a line of its own, which the editor reads the same way.

Revision ID: 694b0dbaf5eb
Revises: 3f6c2d8e9a14
Create Date: 2026-10-06 20:30:00.000000

"""

import json
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "694b0dbaf5eb"
down_revision: str | Sequence[str] | None = "3f6c2d8e9a14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BILIBILI_VIDEO = re.compile(
    r"https?://(?:www\.|m\.)?bilibili\.com/video/BV[0-9A-Za-z]{10}\S*"
)


def video_block(url: str) -> dict:
    """The editor block that shows ``url``: the player, or a link to it."""
    if BILIBILI_VIDEO.fullmatch(url):
        return {"type": "video", "attrs": {"src": url}}
    link = {"type": "link", "attrs": {"href": url, "target": "_blank"}}
    return {
        "type": "paragraph",
        "content": [{"type": "text", "text": url, "marks": [link]}],
    }


def editor_document(description: str) -> dict | None:
    try:
        parsed = json.loads(description)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) and parsed.get("type") == "doc" else None


def with_video(description: str | None, video_url: str) -> str:
    """``description`` with ``video_url`` as its first block."""
    url = video_url.strip()
    text = description or ""
    document = editor_document(text)
    if document is not None:
        content = document.get("content") or []
        return json.dumps(
            {**document, "content": [video_block(url), *content]}, ensure_ascii=False
        )
    return f"{url}\n\n{text}" if text.strip() else url


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, description, video_url FROM task "
            "WHERE video_url IS NOT NULL AND btrim(video_url) <> ''"
        )
    ).all()
    for task_id, description, video_url in rows:
        bind.execute(
            sa.text("UPDATE task SET description = :description WHERE id = :id"),
            {"id": task_id, "description": with_video(description, video_url)},
        )
    op.drop_column("task", "video_url")


def downgrade() -> None:
    # The links stay in the descriptions; the column comes back empty.
    op.add_column("task", sa.Column("video_url", sa.String(), nullable=True))
