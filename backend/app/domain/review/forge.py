"""Forge capabilities and operations used by the acceptance flow (#363).

Providers declare checks independently of their merge mechanism. A new provider
implements these operations and adds its binding in resolve(); shared actor,
vote, and viewed-head checks do not depend on the provider's identity.
"""

import enum
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError

if TYPE_CHECKING:
    from app.domain.review.models import AcceptCard
    from app.domain.review.services import AcceptService
    from app.domain.topic.models import Topic

BINDING_UNKNOWN_MESSAGE = "采纳未完成：暂时无法读取项目的代码仓库，稍后重试采纳"
FORGE_KIND_UNKNOWN = "unknown"
FORGE_UNKNOWN_DECLARATION = "ℹ️ 暂时读不出这个项目的托管方：采纳先等一下，稍后重试"


class ForgeKind(enum.StrEnum):
    github_app = "github_app"
    forgejo = "forgejo"


class ForgeIdentity(enum.StrEnum):
    user = "user"
    platform = "platform"


@dataclass(frozen=True, slots=True)
class ForgeCapabilities:
    """ARCH §4.5 的能力位。产品判断只许读这些，不许读「项目有没有绑外部仓库」那个
    布尔（不变量 I21②）——那个事实进得来，但要以 `has_external_remote` 这一位的身
    份进来，由实现去读，不由产品判断分叉。"""

    #: 这个托管方跑不跑检查、平台能不能读到结论。
    reports_checks: bool
    #: 改动在外部有没有一个可以被人打开的提案页。
    hosts_proposals: bool
    #: 我们有没有写那个远端的凭据。
    can_write_remote: bool
    #: 项目有没有一个外部 git 远端。和 `can_write_remote` 分开，因为「没填地址」
    #: 和「填了地址我们推不动」是两句不同的话，而 `pushes_to_external_remote`
    #: 在这两种情况下都是否，压成一位就说不出后一句。
    has_external_remote: bool
    #: 有远端 ∧ 写得动。
    pushes_to_external_remote: bool
    #: 提案与合并署谁的名。
    identity: ForgeIdentity


class Forge(ABC):
    kind: ForgeKind
    declaration = ""

    def __init__(self, capabilities: ForgeCapabilities) -> None:
        self.capabilities = capabilities

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


FORGES: dict[ForgeKind, type[Forge]] = {
    ForgeKind.github_app: GitHubForge,
    ForgeKind.forgejo: ForgejoForge,
}


async def resolve(
    *,
    project_id: uuid.UUID,
    session: AsyncSession,
    proposal_url: str | None = None,
) -> Forge:
    """Resolve the authoritative forge; an unreadable binding never falls local."""
    from app.domain.project.forge import binding_for_project, tokens_for_project

    try:
        binding = await binding_for_project(project_id, session)
    except Exception as exc:  # noqa: BLE001 — cannot pick a lane blind
        raise ValidationError(BINDING_UNKNOWN_MESSAGE) from exc
    if binding is None:
        raise ValidationError("项目没有代码仓库，采纳尚不可用")
    if proposal_url and urlsplit(proposal_url).netloc != urlsplit(binding.url).netloc:
        raise ValidationError("评审所属的托管服务与项目仓库不一致")
    try:
        provider = FORGES[ForgeKind(binding.kind)]
    except (ValueError, KeyError) as exc:
        raise ValidationError("项目的代码托管类型无法识别") from exc
    try:
        writable = await tokens_for_project(project_id, session) is not None
    except Exception as exc:  # noqa: BLE001 — cannot declare unreadable capabilities
        raise ValidationError(BINDING_UNKNOWN_MESSAGE) from exc
    return provider(
        ForgeCapabilities(
            reports_checks=True,
            hosts_proposals=True,
            can_write_remote=writable,
            has_external_remote=True,
            pushes_to_external_remote=writable,
            identity=(
                ForgeIdentity.user
                if binding.kind == "github_app"
                else ForgeIdentity.platform
            ),
        )
    )
