"""Forge capabilities and operations used by the acceptance flow (#363).

Providers declare checks independently of their merge mechanism. A new provider
implements these operations and adds its binding in resolve(); shared actor,
vote, and viewed-head checks do not depend on the provider's identity.
"""

import enum
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, NoReturn
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError

if TYPE_CHECKING:
    from app.domain.review.models import AcceptCard
    from app.domain.review.services import AcceptService
    from app.domain.topic.models import Topic

BINDING_UNKNOWN_MESSAGE = "采纳未完成：暂时无法读取项目的代码仓库，稍后重试采纳"


class ForgeKind(enum.StrEnum):
    github_app = "github_app"
    forgejo = "forgejo"


class Forge(ABC):
    kind: str
    has_external_checks: bool
    note: str

    @abstractmethod
    async def accept(
        self,
        service: "AcceptService",
        card: "AcceptCard",
        topic: "Topic",
        decided_by: str,
        *,
        seen_head: str | None,
    ) -> "AcceptCard":
        """Merge through this provider, or refuse without changing providers."""

    @abstractmethod
    async def refresh_unseen_head(
        self,
        service: "AcceptService",
        card: "AcceptCard",
        topic: "Topic",
        action: str,
    ) -> NoReturn:
        """Refresh a proposal with no displayed revision and require another look."""
        raise NotImplementedError

    @abstractmethod
    async def poll(
        self,
        service: "AcceptService",
        card: "AcceptCard",
        topic: "Topic",
        *,
        chat_service,
        runner,
    ) -> None:
        """Observe this provider's checks and merges."""

    @abstractmethod
    async def merge_despite_checks(
        self,
        service: "AcceptService",
        card: "AcceptCard",
        topic: "Topic",
        decided_by: str,
        *,
        seen_head: str,
        reason: str,
    ) -> "AcceptCard":
        """Execute an authorized override on the revision the human saw."""


class GitHubForge(Forge):
    kind = ForgeKind.github_app
    has_external_checks = True
    note = ""

    async def accept(self, service, card, topic, decided_by, *, seen_head):
        return await service._accept_github(
            card, topic, decided_by, seen_head=seen_head
        )

    async def refresh_unseen_head(self, service, card, topic, action) -> NoReturn:
        await service._refresh_github_unseen_head(card, topic, action)

    async def poll(self, service, card, topic, *, chat_service, runner) -> None:
        await service._advance_github_card(
            card, topic, chat_service=chat_service, runner=runner
        )

    async def merge_despite_checks(
        self,
        service,
        card,
        topic,
        decided_by,
        *,
        seen_head,
        reason,
    ):
        return await service._override_github_checks(
            card, topic, decided_by, seen_head=seen_head, reason=reason
        )


class ForgejoForge(GitHubForge):
    kind = ForgeKind.forgejo


FORGES: dict[ForgeKind, Forge] = {
    ForgeKind.github_app: GitHubForge(),
    ForgeKind.forgejo: ForgejoForge(),
}


async def resolve(
    *,
    project_id: uuid.UUID,
    session: AsyncSession,
    proposal_url: str | None = None,
) -> Forge:
    """Resolve the authoritative forge; an unreadable binding never falls local."""
    from app.domain.project.forge import binding_for_project

    try:
        binding = await binding_for_project(project_id, session)
    except Exception as exc:  # noqa: BLE001 — cannot pick a lane blind
        raise ValidationError(BINDING_UNKNOWN_MESSAGE) from exc
    if binding is None:
        raise ValidationError("项目没有代码仓库，采纳尚不可用")
    if proposal_url and urlsplit(proposal_url).netloc != urlsplit(binding.url).netloc:
        raise ValidationError("评审所属的托管服务与项目仓库不一致")
    try:
        return FORGES[ForgeKind(binding.kind)]
    except (ValueError, KeyError) as exc:
        raise ValidationError("项目的代码托管类型无法识别") from exc
