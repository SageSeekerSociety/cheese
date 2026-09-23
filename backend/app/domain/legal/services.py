from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.legal.documents import DOCUMENTS, sha256_of
from app.domain.legal.models import UserConsent

CONSENT_METHODS = frozenset({"checkbox", "dialog"})


class ConsentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        user_id: int,
        accepted: dict[str, str],
        method: str,
        entry: str,
        ip: str,
        user_agent: str,
    ) -> None:
        """Append one row per accepted document. Flushes only: the caller's
        transaction decides whether it lands, so an account whose creation
        fails leaves no consent behind and a created one never lacks it."""
        if method not in CONSENT_METHODS:
            raise ValueError("CONSENT_METHOD_INVALID")
        now = datetime.now(UTC)
        for document, version in accepted.items():
            doc = DOCUMENTS.get(document)
            if doc is None or doc.get(version) is None:
                raise ValueError("CONSENT_UNKNOWN_DOCUMENT")
            self._session.add(
                UserConsent(
                    user_id=user_id,
                    document=document,
                    version=version,
                    content_sha256=sha256_of(document, version),
                    accepted_at=now,
                    method=method,
                    entry=entry,
                    ip=ip[:512],
                    user_agent=user_agent[:1024],
                )
            )
        await self._session.flush()

    async def pending(self, user_id: int) -> list[str]:
        """Documents whose current rules this user has not accepted yet."""
        rows = await self._session.execute(
            select(UserConsent.document, UserConsent.version).where(
                UserConsent.user_id == user_id
            )
        )
        accepted: dict[str, set[str]] = {}
        for document, version in rows:
            accepted.setdefault(document, set()).add(version)
        return [
            key
            for key, doc in DOCUMENTS.items()
            if not doc.satisfies(accepted.get(key, set()))
        ]
