"""托管方（forge）：项目的改动落在哪里，以及那个地方能做什么。

一个 forge 不是按「上游地址长得像不像 github.com」挑出来的，而是按**能力位**挑
出来的（ARCH §4.5）。能力位全部从项目今天**已经有的事实**算出来
（`ProjectForgeFacts`）——不存成一条新的能力记录：存下来就是同一个事实的第二份
声明，还会多出一个「记录与事实不一致」的窗口。`capabilities_of` 是纯函数。

分三档，中间那一档以前在代码里根本不存在：

- ``GitHubForge``：报检查、托管提案页、推外部远端。
- ``ExternalRemoteForge``：不报检查、没有提案页，**照样推回那个远端**
  （gitee、校内 GitLab、自建）。
- ``PlatformForge``：什么都没绑，平台自己的仓库就是终点。

中间这一档不存在时会发生什么：一个用校内 GitLab 的老师填了自己的仓库地址、点了
同步、看见历史进来了，于是合理地认为这是双向的。此后每一次采纳都只落在平台自己
的仓库里，他的 GitLab 一个 commit 都收不到，**没有任何一句话告诉他**。

反过来，一个填了地址却没给我们写权限的项目，会拿到一个永远推不上去的 forge——
所以 ``pushes_to_external_remote`` 是「有远端」和「写得动」的**合取**，缺一半就落
``PlatformForge``。但它**不能说「本项目未接外部仓库」**：那位老师明明填了地址，卡
当着他的面说他没填，是同一种沉默换了一句假话。``has_external_remote`` 因此也是一个
能力位——少了它，「没有远端」和「有远端但写不动」在能力位上一模一样，实现无从分辨
该说哪句。

**用户不为了用我们而改任何东西**（结论 50，不变量 I21c）：三档都得能开提案、读
结论、合并，没有一条路以「请去 GitHub 开个 X」结束。检查结论只**读** forge 的；
forge 没规则可读时才按平台侧的项目规则判（`project/protection.py`）。

**托管方身份在卡生成的那一刻就在卡上**（I23）：`declaration` 与能力位随
`AcceptService.describe()` 下发，不是人点完采纳之后才补写上去的一条 note。
"""

import enum
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, NoReturn
from urllib.parse import urlsplit

from app.core.errors import ValidationError

if TYPE_CHECKING:
    from app.domain.review.models import AcceptCard
    from app.domain.review.services import AcceptService
    from app.domain.topic.models import Topic

FACTS_UNKNOWN_MESSAGE = "采纳未完成：暂时读不出项目的托管方能力，稍后重试采纳"

#: 读路径上事实读不出来时卡上那一档。它不是第四个 forge —— 没有哪个实现承担它，
#: `resolve()` 也永远不会返回它；它是「这张卡这会儿说不出自己的托管方是谁」这件
#: 事本身，如实写在卡面上。采纳照旧 fail-closed，拒的时候说的是上面那句话。
FORGE_KIND_UNKNOWN = "unknown"
FORGE_UNKNOWN_DECLARATION = "ℹ️ 暂时读不出这个项目的托管方：采纳先等一下，稍后重试"


class ForgeKind(enum.StrEnum):
    github_app = "github_app"
    #: 有 git 远端，但那个远端不报检查、也不托管提案页。
    external_remote = "external_remote"
    platform = "platform"


class ForgeIdentity(enum.StrEnum):
    """提案与合并署谁的名。"""

    #: 用户自己的凭据（GitHub 上的 PR 属于把活交出来的那个人）。
    user = "user"
    #: 平台的 App / 平台自己的仓库。
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


@dataclass(frozen=True, slots=True)
class ProjectForgeFacts:
    """能力位算自这三个事实，它们今天都已经在项目里了。"""

    #: 平台的 GitHub App 在这个项目的上游仓库上解析得出安装。
    github_app_installed: bool
    #: 项目有一个外部 git 远端。
    has_external_remote: bool
    #: 我们手上有写那个远端的凭据。
    remote_write_credential: bool


def capabilities_of(facts: ProjectForgeFacts) -> ForgeCapabilities:
    """能力位，纯函数，无 IO。"""
    return ForgeCapabilities(
        reports_checks=facts.github_app_installed,
        hosts_proposals=facts.github_app_installed,
        can_write_remote=facts.remote_write_credential,
        has_external_remote=facts.has_external_remote,
        pushes_to_external_remote=(
            facts.has_external_remote and facts.remote_write_credential
        ),
        identity=(
            ForgeIdentity.user if facts.github_app_installed else ForgeIdentity.platform
        ),
    )


class Forge(ABC):
    kind: ForgeKind

    def __init__(self, capabilities: ForgeCapabilities) -> None:
        #: 解析这一次时算出来的能力位 —— 卡上的那一份就是它，没有第二份。
        self.capabilities = capabilities

    @property
    def declaration(self) -> str:
        """这个托管方在人点采纳**之前**就写在卡上的一句话（I23）。

        算自 `self.capabilities`：同一档里能力位不同，要说的话就不同。GitHub 那一
        档不需要说什么，卡上有提案页链接。
        """
        return ""

    @classmethod
    @abstractmethod
    def serves(cls, capabilities: ForgeCapabilities) -> bool:
        """这组能力位该由本实现承担吗？

        三个实现的谓词合起来对任意一组能力位**恰好**命中一个（`forge_for` 会检查
        这一点），所以加第四个提供者是加一个类，不是在 `resolve` 里加一个分支。
        """

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

    @classmethod
    def serves(cls, capabilities: ForgeCapabilities) -> bool:
        return capabilities.hosts_proposals

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


class ExternalRemoteForge(Forge):
    """有 git 远端、但那个远端既不报检查也不托管提案页的项目。

    采纳和 GitHub 那一档是同一个产品：squash 进平台仓库的 main，**并把 main 推回
    项目自己的远端**。少掉的只有外部检查和提案页，这两样是那个远端本来就没有的，
    不是我们降级给他的。
    """

    kind = ForgeKind.external_remote

    @property
    def declaration(self) -> str:
        return "ℹ️ 本项目的远端不报检查：采纳即合并并推回该远端（无提案页、无外部 CI）"

    @classmethod
    def serves(cls, capabilities: ForgeCapabilities) -> bool:
        return (
            not capabilities.hosts_proposals and capabilities.pushes_to_external_remote
        )

    async def accept(self, service, card, topic, decided_by, *, seen_head):
        return await service._accept_external_remote(card, topic, decided_by)

    async def refresh_unseen_head(self, service, card, topic, action) -> NoReturn:
        raise ValidationError("本项目的远端没有提案页，没有可刷新的东西")

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


class PlatformForge(Forge):
    """采纳的终点就是平台自己的仓库——因为没有别的仓库，或者有而我们推不动。

    两种情况的动作一模一样（squash 进平台仓库的 main，不推任何地方），所以是同一
    个实现；不一样的只有卡上那句话，而那句话必须分得清：一个填了校内 GitLab 地址
    的项目被告知「本项目未接外部仓库」，比什么都不说更糟。
    """

    kind = ForgeKind.platform

    @property
    def declaration(self) -> str:
        if self.capabilities.has_external_remote:
            return (
                "ℹ️ 本项目的远端这次不会收到改动：平台没有写它的凭据，采纳只合并进"
                "平台仓库的 main（无提案页、无外部 CI）"
            )
        return (
            "ℹ️ 本项目未接外部仓库：采纳即合并进平台仓库的 main（无提案页、无外部 CI）"
        )

    @classmethod
    def serves(cls, capabilities: ForgeCapabilities) -> bool:
        return (
            not capabilities.hosts_proposals
            and not capabilities.pushes_to_external_remote
        )

    async def accept(self, service, card, topic, decided_by, *, seen_head):
        return await service._accept_platform(card, topic, decided_by)

    async def refresh_unseen_head(self, service, card, topic, action) -> NoReturn:
        raise ValidationError("本项目没有提案页，没有可刷新的东西")

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


#: 注册表。加一个提供者 = 往这里加一个类并写它的 `serves`；采纳流程一行不改
#: （#363 自己的判据）。
FORGES: tuple[type[Forge], ...] = (GitHubForge, ExternalRemoteForge, PlatformForge)


def _is_github_proposal(proposal_url: str | None) -> bool:
    """这条提案页链接是不是 GitHub 上的一个 PR。"""
    if not proposal_url:
        return False
    return urlsplit(proposal_url).hostname == "github.com"


def forge_for(capabilities: ForgeCapabilities) -> Forge:
    """承担这组能力位的那个实现。恰好一个，否则注册表自己有洞。"""
    matched = [cls for cls in FORGES if cls.serves(capabilities)]
    if len(matched) != 1:  # pragma: no cover - a registry bug, not an input
        raise RuntimeError(
            f"{len(matched)} forges serve {capabilities}; exactly one must"
        )
    return matched[0](capabilities)


async def resolve(
    *,
    project_id: uuid.UUID,
    facts: Callable[[uuid.UUID], Awaitable[ProjectForgeFacts]],
    proposal_url: str | None = None,
) -> Forge:
    """这次采纳走哪个托管方。读不出事实就停住，绝不摸黑挑一条。

    **分派只有最后那一句**：能力位交给注册表。「这个 URL 像不像 github.com」不在
    分派里（ARCH §4.5）——它是一条**事实**：卡上已经有一个打得开的 GitHub 提案页，
    就说明有一个托管方在托管这次改动，它在一个外部远端上，而且那一页是我们自己推
    上去的。所以它在算能力位**之前**并进事实，判据留在事实那一侧，分派保持纯粹按
    能力位；加一个提供者仍然只是往 `FORGES` 里加一个类。

    这一条为什么非要在：今天写 `pr_url` 的只有 GitHub 那条路（`pr_publish.py`、
    `services.py`、`room_task/services.py`），而凭据一时读成空不能把一次 PR 采纳变
    成一次本地合并（#362 的另一条进路）。别处来的提案页什么都没证明，照常按项目的
    事实分派。

    事实整个读不出来（抛异常）时仍然停住，哪怕卡上有提案页：停住是可重试的，而摸黑
    挑一条不是——它照样绝不会变成一次本地合并。

    并事实这一步在这里而不在 `facts` 那个回调里：回调的答案是**项目**的事实、按项
    目缓存，把某一张卡的提案页并进去会跟着缓存跑到同一个项目的别的卡上。
    """
    try:
        observed = await facts(project_id)
    except Exception as exc:  # noqa: BLE001 — cannot pick a lane blind
        raise ValidationError(FACTS_UNKNOWN_MESSAGE) from exc
    if _is_github_proposal(proposal_url):
        observed = replace(
            observed,
            github_app_installed=True,
            has_external_remote=True,
            remote_write_credential=True,
        )
    return forge_for(capabilities_of(observed))
