"""Keeping a document's mini-apps: their keys, their versions, their pictures."""

import secrets
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.library.blobs import LocalBlobs
from app.domain.web_snippet.check import Ran
from app.domain.web_snippet.models import WebSnippet, WebSnippetVersion

#: The shell new code is written for and checked on (`frontend/miniapp/`).
SHELL = 1
#: The letters a key is spelled with: no 0/o, 1/l/i, which a person copying
#: one by eye would mix up.
_KEY_LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"
KEY_LENGTH = 8


def _new_key() -> str:
    return "".join(secrets.choice(_KEY_LETTERS) for _ in range(KEY_LENGTH))


def _blobs() -> LocalBlobs:
    return LocalBlobs(Path(settings.workspace_root))


def shot_name(width: int, theme: str) -> str:
    return f"{width}-{theme}"


class Snippets:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def by_key(self, key: str) -> WebSnippet | None:
        return await self.db.scalar(select(WebSnippet).where(WebSnippet.key == key))

    async def version(
        self, snippet: WebSnippet, version: int
    ) -> WebSnippetVersion | None:
        return await self.db.scalar(
            select(WebSnippetVersion).where(
                WebSnippetVersion.snippet_id == snippet.id,
                WebSnippetVersion.version == version,
            )
        )

    async def require(self, key: str) -> WebSnippet:
        snippet = await self.by_key(key)
        if snippet is None:
            raise NotFoundError(say("appNotFound"))
        return snippet

    async def require_version(
        self, snippet: WebSnippet, version: int
    ) -> WebSnippetVersion:
        found = await self.version(snippet, version)
        if found is None:
            raise NotFoundError(say("appVersionNotFound", version=version))
        return found

    async def versions(self, snippet: WebSnippet) -> list[WebSnippetVersion]:
        rows = await self.db.scalars(
            select(WebSnippetVersion)
            .where(WebSnippetVersion.snippet_id == snippet.id)
            .order_by(WebSnippetVersion.version)
        )
        return list(rows)

    async def latest(self, snippet: WebSnippet) -> int:
        return (
            await self.db.scalar(
                select(func.max(WebSnippetVersion.version)).where(
                    WebSnippetVersion.snippet_id == snippet.id
                )
            )
            or 0
        )

    async def create(self, document_id: uuid.UUID, author: str) -> WebSnippet:
        """A new snippet of the document, under a key nobody has."""
        for _ in range(5):
            snippet = WebSnippet(
                key=_new_key(), document_id=document_id, created_by=author
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(snippet)
            except IntegrityError:
                continue
            return snippet
        # i18n-exempt: an invariant broken in the database, a 500 no reader is told
        raise RuntimeError("no free snippet key after five tries")

    async def add_version(
        self,
        snippet: WebSnippet,
        *,
        project_id: uuid.UUID,
        code: str,
        note: str,
        author: str,
        requested_by: str | None,
        ran: Ran | None,
    ) -> WebSnippetVersion:
        """The next version of ``snippet``, with what the platform's browser
        saw of it (``ran``), or unchecked when no browser could be had."""
        number = await self.latest(snippet) + 1
        shots: dict[str, str] = {}
        if ran is not None:
            for name, png in ran.shots.items():
                key = f".web-snippets/{project_id}/{snippet.id}/{number}-{name}.png"
                _blobs().put(key, png)
                shots[name] = key
        version = WebSnippetVersion(
            snippet_id=snippet.id,
            version=number,
            code=code,
            shell=SHELL,
            note=note,
            author=author,
            requested_by=requested_by,
            checked=ran is not None,
            heights={str(w): h for w, h in (ran.heights if ran else {}).items()},
            shots=shots,
        )
        self.db.add(version)
        await self.db.flush()
        return version

    @staticmethod
    def describe(snippet: WebSnippet, version: WebSnippetVersion) -> dict:
        """A version as the version menu and the tools show it."""
        return {
            "key": snippet.key,
            "version": version.version,
            "note": version.note,
            "author": version.author,
            "requested_by": version.requested_by,
            "checked": version.checked,
            "created_at": version.created_at.isoformat(),
        }

    @staticmethod
    def shot(version: WebSnippetVersion, name: str) -> bytes | None:
        key = version.shots.get(name)
        return _blobs().get(key) if key else None
