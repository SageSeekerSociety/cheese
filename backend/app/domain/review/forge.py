"""Forge capabilities and operations used by the acceptance flow (#363).

Providers declare checks independently of their merge mechanism. A new provider
implements these operations and adds its binding in resolve(); shared actor,
vote, and viewed-head checks do not depend on the provider's identity.
"""

import enum
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, NoReturn
from urllib.parse import urlsplit

from app.core.errors import ValidationError

if TYPE_CHECKING:
    from app.domain.review.models import AcceptCard
    from app.domain.review.services import AcceptService
    from app.domain.topic.models import Topic

PLATFORM_FORGE_NOTE = (
    "ℹ️ 本项目未接 GitHub：采纳即合并进平台仓库的 main（无 PR、无外部 CI）"
)
BINDING_UNKNOWN_MESSAGE = "采纳未完成：暂时无法判定项目的 GitHub 绑定状态，稍后重试采纳"


class ForgeKind(enum.StrEnum):
    github_app = "github_app"
    platform = "platform"


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


class PlatformForge(Forge):
    kind = ForgeKind.platform
    has_external_checks = False
    note = PLATFORM_FORGE_NOTE

    async def accept(self, service, card, topic, decided_by, *, seen_head):
        return await service._accept_platform(card, topic, decided_by, note=self.note)

    async def refresh_unseen_head(self, service, card, topic, action) -> NoReturn:
        raise ValidationError("本项目未接 GitHub，没有可刷新的 PR")

    async def poll(self, service, card, topic, *, chat_service, runner) -> None:
        return

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
        raise ValidationError("本项目没有需要人工放行的外部检查")


FORGES: dict[ForgeKind, Forge] = {
    ForgeKind.github_app: GitHubForge(),
    ForgeKind.platform: PlatformForge(),
}


async def resolve(
    *,
    project_id: uuid.UUID,
    is_github_bound: Callable[[uuid.UUID], Awaitable[bool]],
    proposal_url: str | None = None,
) -> Forge:
    """Resolve the authoritative forge; an unreadable binding never falls local."""
    # An existing proposal retains its forge when the project loses credentials.
    # Otherwise a temporary disconnect could turn a PR acceptance into a local merge.
    if proposal_url and urlsplit(proposal_url).hostname == "github.com":
        return FORGES[ForgeKind.github_app]
    try:
        bound = await is_github_bound(project_id)
    except Exception as exc:  # noqa: BLE001 — cannot pick a lane blind
        raise ValidationError(BINDING_UNKNOWN_MESSAGE) from exc
    return FORGES[ForgeKind.github_app if bound else ForgeKind.platform]
