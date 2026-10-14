"""Web snippets: the code of a document's mini-apps (`:::app`), by version.

A snippet is a piece of HTML and script 芝士 wrote, run inside the platform's
shell in an isolated frame. It belongs to one document — the one it was made
for — and is reached through that document's permissions wherever it shows. Its
code never changes: each change is a new version, and a document names the
version it shows (`:::app <key>@<version>`), so the document's own history
holds which code it showed when.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


class WebSnippet(UuidPk, Base):
    __tablename__ = "web_snippets"
    __table_args__ = (UniqueConstraint("key", name="uq_web_snippets_key"),)

    #: What a document writes to name it: short, unique across the platform, so
    #: a block copied into another document still names this one.
    key: Mapped[str] = mapped_column(String(16))
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="CASCADE",
            name="fk_web_snippets_document_id_documents",
        ),
        index=True,
    )
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class WebSnippetVersion(UuidPk, Base):
    __tablename__ = "web_snippet_versions"
    __table_args__ = (
        UniqueConstraint(
            "snippet_id", "version", name="uq_web_snippet_versions_snippet_id_version"
        ),
    )

    snippet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "web_snippets.id",
            ondelete="CASCADE",
            name="fk_web_snippet_versions_snippet_id_web_snippets",
        ),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(Text)
    #: The major version of the shell it was written for and checked on: a new
    #: major shell leaves this code on the one it knew.
    shell: Mapped[int] = mapped_column(Integer)
    #: What changed, in a sentence, for the version menu.
    note: Mapped[str] = mapped_column(String(200), default="")
    author: Mapped[str] = mapped_column(String(128))
    #: Who asked for the change, when someone did.
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Whether the platform's browser ran it before it was kept. False when no
    #: browser could be had; it is then kept unchecked, with no pictures.
    checked: Mapped[bool] = mapped_column(default=False)
    #: Its height in pixels at each width it was run at, by width ("400").
    heights: Mapped[dict] = mapped_column(JSONB, default=dict)
    #: Where its pictures are kept, by "<width>-<theme>".
    shots: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
