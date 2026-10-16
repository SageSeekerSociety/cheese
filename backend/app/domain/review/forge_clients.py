"""按项目的绑定造一个托管方客户端 —— 只有第 4 层及以上够得着的两次构造。

`proposal_client` 造写提案的那个（开 PR、评、合），`status_client` 造读状态的那个。
两件事都同时要两样东西：本层 `github_pr` / `forgejo_pr` 里的客户端类，和第 2 层
`project.forge` 里「这个项目绑的是谁、令牌领得到吗」那两次读。第 2 层够不到第 4 层，
所以这对构造函数只能住在第 4 层及以上 —— `project` 自己从不构造客户端，它只是替别人
造，那两条向上的边（C5 的 `domain-layers`）就是这么来的
（`docs/manual/dev/architecture.md` 第十三笔）。

`compare_status` 是同一个读法收窄成一问：`base` 与 `head` 谁含谁。
`repository.forge_files` 判「这个提交交付过没有」要问这一句，第 3 层够不着客户端，
所以口子开在它自己的 `ports.py`（`CompareStatus`），由组装那条路由的地方把本函数递进去。
"""

import uuid
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import release_read_session
from app.core.errors import UpstreamUnavailableError
from app.core.sentences import say
from app.domain.project.forge import binding_for_project, tokens_for_project
from app.domain.project.github_app import GitHubAppTokens
from app.domain.review.forgejo_pr import ForgejoClient, ForgejoPRClient
from app.domain.review.github_pr import GitHubPRClient, default_client


async def proposal_client(project_id: uuid.UUID, session: AsyncSession):
    binding = await binding_for_project(project_id, session)
    if binding is None:
        return None
    tokens = await tokens_for_project(project_id, session)
    if tokens is None:
        raise UpstreamUnavailableError(say("forgeCredentialsMissing"))
    owner, repo = binding.repo.split("/", 1)
    if binding.kind == "github_app":
        return GitHubPRClient(
            owner, repo, cast(GitHubAppTokens, tokens), api_base=binding.api_url
        )
    return ForgejoPRClient(owner, repo, tokens, api_base=binding.api_url)


async def status_client(project_id: uuid.UUID, session: AsyncSession):
    binding = await binding_for_project(project_id, session)
    if binding is None:
        raise UpstreamUnavailableError(say("forgeNoRepository"))
    if binding.kind == "github_app":
        return default_client()
    if binding.kind == "forgejo":
        return ForgejoClient(binding.api_url)
    raise UpstreamUnavailableError(say("forgeKindUnknown"))


async def compare_status(
    project_id: uuid.UUID,
    session: AsyncSession,
    *,
    base: str,
    head: str,
    release_session: bool = False,
) -> str | None:
    """`base` 与 `head` 谁含谁：GitHub 直接答 `status`，Forgejo 数两边的提交。

    答的是 `identical`、`ahead`、`behind`、`diverged` 之一（`None` 是托管方答了个
    认不出的形状）。`release_session` 和 `repository_data` 同一个意思：读事务在等
    远端之前先把连接放掉。
    """
    binding = await binding_for_project(project_id, session)
    if binding is None:
        raise UpstreamUnavailableError(say("forgeNoRepository"))
    tokens = await tokens_for_project(project_id, session)
    if tokens is None:
        raise UpstreamUnavailableError(say("forgeCredentialsMissing"))
    owner, repo = binding.repo.split("/", 1)
    if release_session:
        await release_read_session(session)
    token, _ = await tokens.installation_token()
    client = await status_client(project_id, session)
    if release_session:
        await release_read_session(session)
    return await client.compare_status(
        owner=owner, repo=repo, base=base, head=head, token=token
    )
