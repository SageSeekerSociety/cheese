"""Project credentials authenticate the fixed agent of the project's root room.

The principal does not change with the destination or inherit the issuer's
roles. Its current memberships grant access; issuance creates no membership.
The signed project and credential generation bound the credential. Revoking
increments that generation and invalidates every previous project credential.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.core.sandbox_auth import (
    AGENT_CREDENTIAL_DEFAULT_TTL_DAYS,
    AGENT_CREDENTIAL_MAX_TTL_DAYS,
    mint_project_agent_credential,
    project_agent_claims,
)
from app.domain.identity.handles import topic_agent_handle
from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.repositories import TopicRepository

_EPOCH_KEY = "agent_credential_epoch"
_DAY_S = 86400


def credential_epoch_of(project: Project) -> int:
    """The project's current credential generation. A project that has never
    revoked anything sits at 0, which is also what the first issued credential
    carries — so no backfill is needed for projects that predate this."""
    raw = (project.settings or {}).get(_EPOCH_KEY)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return raw


@dataclass(frozen=True)
class IssuedCredential:
    """An issue's one and only chance to see the secret."""

    token: str
    project_id: uuid.UUID
    epoch: int
    expires_at: datetime


class ProjectAgentCredentialService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._projects = ProjectRepository(session)
        self._topics = TopicRepository(session)

    async def _get_or_404(self, project_id: uuid.UUID) -> Project:
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError("Project not found")
        return project

    async def agent_handle(self, project_id: uuid.UUID) -> str | None:
        """The project's fixed agent identity, independent of the target room.

        Its existing memberships grant access; holding this credential never
        creates a membership or borrows another room's agent seat.
        """
        project = await self._projects.get(project_id)
        if project is None or project.root_topic_id is None:
            return None
        return topic_agent_handle(project.root_topic_id)

    async def issue(
        self, *, project_id: uuid.UUID, expires_in_days: int | None = None
    ) -> IssuedCredential:
        """Mint a credential for the project. The plaintext is returned here and
        never again — nothing stores it, so a lost credential is re-issued (and
        the old one revoked), never recovered."""
        days = (
            AGENT_CREDENTIAL_DEFAULT_TTL_DAYS
            if expires_in_days is None
            else expires_in_days
        )
        if days < 1 or days > AGENT_CREDENTIAL_MAX_TTL_DAYS:
            raise ValidationError(
                f"有效期必须在 1 到 {AGENT_CREDENTIAL_MAX_TTL_DAYS} 天之间"
            )
        project = await self._get_or_404(project_id)
        epoch = credential_epoch_of(project)
        token = mint_project_agent_credential(
            project_id=str(project_id), epoch=epoch, ttl_s=days * _DAY_S
        )
        claims = project_agent_claims(token)
        # Unreachable in practice — we just minted it — but the expiry the caller
        # is told must come from the credential itself, not from a second
        # computation that could drift from it.
        if claims is None:  # pragma: no cover - defensive
            raise ValidationError("凭证签发失败")
        return IssuedCredential(
            token=token,
            project_id=project_id,
            epoch=epoch,
            expires_at=claims.expires_at,
        )

    async def revoke(self, *, project_id: uuid.UUID) -> int:
        """Retire every credential issued for this project so far, and return the
        new generation. Idempotent in effect, not in value: calling twice simply
        retires nothing the second time."""
        project = await self._get_or_404(project_id)
        epoch = credential_epoch_of(project) + 1
        await self._projects.set_settings(
            project, {**(project.settings or {}), _EPOCH_KEY: epoch}
        )
        return epoch

    async def current_epoch(self, *, project_id: uuid.UUID) -> int:
        return credential_epoch_of(await self._get_or_404(project_id))

    async def authenticate(self, token: str, *, project_id: uuid.UUID) -> bool:
        """Does ``token`` authorize acting as 芝士 in this project right now?

        Four independent conditions, all required: it is one of ours (signature),
        it has not expired, it names THIS project, and it belongs to the current
        generation. Anything else is false — never an exception, because this
        runs on whatever a caller put in the header.
        """
        claims = project_agent_claims(token)
        if claims is None:
            return False
        if claims.project_id != str(project_id):
            return False
        project = await self._projects.get(project_id)
        if project is None:
            return False
        return claims.epoch == credential_epoch_of(project)

    async def project_of_request(
        self, *, project_id: str | None, topic_id: str | None
    ) -> uuid.UUID | None:
        """Which project a cheese-gated URL acts on, from its path ids alone.

        The gate matches ``/api/projects/{id}/…`` and ``/api/topics/{id}/…``; the
        second names its project only through the topic, so this is the one place
        that read lives. ``None`` means the request has no project context, which
        must fail closed — a credential is scoped to a project, so a request that
        names none is a request it cannot speak for.
        """
        if project_id is not None:
            return _as_uuid(project_id)
        if topic_id is None:
            return None
        parsed = _as_uuid(topic_id)
        if parsed is None:
            return None
        topic = await self._topics.get(parsed)
        return topic.project_id if topic is not None else None


def _as_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
