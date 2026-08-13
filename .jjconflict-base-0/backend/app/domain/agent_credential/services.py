"""Project agent credentials: issuing, revoking, and authenticating them.

**The credential belongs to the project, not to the person who issued it.**

That is the whole design, and it is the opposite of a delegation. A delegated
credential answers "whose agent is this?" and derives its reach from that
person; this one answers "which project is this?" and derives its reach from the
project. The difference is not philosophical:

* 芝士 is a participant, not a proxy. It has its own context and its own
  mistakes, and filing those under a member's name is the wrong account.
* 芝士 is shared. Five people talk to the same 芝士 in one room, so "whose
  permissions does it use?" has no answer — by the speaker, and its abilities
  flicker with whoever opens their mouth; by the room's creator, and it is
  arbitrarily stronger or weaker than the person sitting next to them.
* Issuers come and go. A lead who issues a credential and then leaves the
  project does not take the project's agent down with them, and does not leave
  behind a credential that quietly still carries their old reach.

So the credential names ``project_id`` and nothing else, and what it can do is
what a member of that project can do — no per-route grant list, no permission
subsetting. Safety is not a smaller permission set; it is that everything the
agent does is written down under 芝士 and is undoable.

**Revocation is stateless.** The credential carries the project's credential
*generation* (``epoch``); the project stores the current generation in its
``settings`` blob. Revoking bumps it, and every credential ever issued for that
project stops verifying on the next request. No table, no migration, no row to
forget to delete — and no way for a revoked credential to survive because some
cache had not caught up.
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

    async def opens_gate(
        self, token: str, *, project_id: str | None, topic_id: str | None
    ) -> bool:
        """Whether this credential opens the cheese write-surface for this URL."""
        if project_agent_claims(token) is None:
            return False
        target = await self.project_of_request(project_id=project_id, topic_id=topic_id)
        if target is None:
            return False
        return await self.authenticate(token, project_id=target)


def _as_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
