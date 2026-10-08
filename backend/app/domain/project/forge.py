"""Resolve the one repository and credentials a project uses."""

import hashlib
import hmac
import logging
import math
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import quote

import httpx
from sqlalchemy import column, func, select, table, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import forge_quota
from app.core.config import settings
from app.core.db import SessionFactory, release_read_session
from app.core.errors import GatewayUnavailableError
from app.core.forge_events import project_secret
from app.core.forge_http import forge_client
from app.core.sentences import say
from app.domain.agent.forgejo_tokens import (
    ForgejoTokens,
    forge_password,
    seal_forge_password,
)
from app.domain.agent.github_app import (
    GitHubAppError,
    GitHubAppTokens,
    github_app_tokens_for_project,
    locate_repo,
    repository_named,
)
from app.domain.project.models import Project, ProjectForge, ProjectGitInstallation
from app.domain.project.repositories import (
    ProjectGitInstallationRepository,
    ProjectRepository,
)
from app.domain.review.forgejo_pr import ForgejoClient, ForgejoPRClient
from app.domain.review.github_pr import (
    GitHubPRClient,
    default_client,
    parse_github_repo,
)


async def binding_for_project(
    project_id: uuid.UUID, session: AsyncSession
) -> ProjectForge | None:
    binding = await session.scalar(
        select(ProjectForge).where(ProjectForge.project_id == project_id)
    )
    if binding is not None and binding.kind == "github_app":
        if await follow_github_rename(project_id, session):
            binding = await session.scalar(
                select(ProjectForge)
                .where(ProjectForge.project_id == project_id)
                .execution_options(populate_existing=True)
            )
    return binding


# How long a GitHub binding's name is trusted before GitHub is asked again
# whether the repository still goes by it. Also how long a rename on GitHub can
# go unnoticed; the ask is a conditional GET, so a repeat costs no quota.
_FOLLOW_EVERY_S = 60.0
_followed_at: dict[uuid.UUID, float] = {}


async def follow_github_rename(
    project_id: uuid.UUID, session: AsyncSession, *, now: bool = False
) -> bool:
    """Bring a GitHub binding up to the repository's current name.

    Renaming a repository on GitHub keeps its id and redirects the old name,
    but everything here is written by name: the token mint (which GitHub
    refuses outright for a stale name), the git and API paths, the card a PR
    was filed under. So the binding follows the id to the name GitHub uses
    now, at most every `_FOLLOW_EVERY_S` unless `now`, and rewrites every place
    that holds the old one. True when something was rewritten.

    The writes go through their own committed session: a reader that rolls
    back (the git relay does, before streaming) must not take the new name
    with it. Never raises — a binding that cannot be checked is served as it is.
    """
    if not settings.github_app_id or not settings.github_app_private_key_path:
        return False
    checked = _followed_at.get(project_id)
    if not now and checked is not None and time.monotonic() - checked < _FOLLOW_EVERY_S:
        return False
    _followed_at[project_id] = time.monotonic()
    log = logging.getLogger(__name__)
    installation = await ProjectGitInstallationRepository(session).get_by_project(
        project_id
    )
    if installation is None:
        return False
    try:
        current = await locate_repo(installation)
    except (GitHubAppError, httpx.HTTPError):
        log.warning("could not look up project %s's GitHub repository", project_id)
        return False
    if current is None:
        return False
    repository_id, name = current
    if (repository_id, name) == (installation.repository_id, installation.repo):
        return False
    try:
        await _record_rename(
            project_id, installation.repo, repository_id, name, session
        )
    except SQLAlchemyError:
        log.warning(
            "could not record project %s's GitHub repository as %s",
            project_id,
            name,
            exc_info=True,
        )
        return False
    if name != installation.repo:
        log.info(
            "project %s's repository %s is now %s", project_id, installation.repo, name
        )
    return True


async def _record_rename(
    project_id: uuid.UUID,
    old: str,
    repository_id: int,
    name: str,
    session: AsyncSession,
) -> None:
    sessions = async_sessionmaker(session.bind, expire_on_commit=False)
    async with sessions() as writer:
        # The reader may already hold one of these rows; wait briefly, then let
        # the next look try again rather than hang the request behind it.
        await writer.execute(text("SET LOCAL lock_timeout = '2s'"))
        await writer.execute(
            update(ProjectGitInstallation)
            .where(ProjectGitInstallation.project_id == project_id)
            .values(repo=name, repository_id=repository_id)
        )
        if name != old:
            await writer.execute(
                update(ProjectForge)
                .where(
                    ProjectForge.project_id == project_id,
                    ProjectForge.kind == "github_app",
                )
                .values(repo=name, url=f"https://github.com/{name}.git")
            )
            project = await writer.get(Project, project_id)
            upstream = (project.settings or {}) if project else {}
            parsed = parse_github_repo(upstream.get("github_repository_url"))
            if project and parsed and "/".join(parsed).lower() == old.lower():
                await ProjectRepository(writer).merge_settings(
                    project, {"github_repository_url": f"https://github.com/{name}"}
                )
            # GitHub's redirect from the old name ends the day someone creates
            # a repository under it, so a task's PR link moves with the rename.
            # A review card's own record moves when the card is next used
            # (`renamed_from`), in the review domain that owns it.
            # The table, not the room_task model: room_task already depends on
            # this module, and the import back would make the two domains a
            # cycle (C3 in .importlinter). Only these two columns are touched.
            tasks = table("tasks", column("project_id"), column("pr_url"))
            before, after = f"https://github.com/{old}/", f"https://github.com/{name}/"
            await writer.execute(
                update(tasks)
                .where(
                    tasks.c.project_id == project_id,
                    tasks.c.pr_url.startswith(before, autoescape=True),
                )
                .values(pr_url=after + func.substr(tasks.c.pr_url, len(before) + 1))
            )
        await writer.commit()


async def renamed_from(project_id: uuid.UUID, name: str, session: AsyncSession) -> bool:
    """Whether `name` is a former name of this project's GitHub repository —
    GitHub still redirects it to the very repository the project is bound to.
    False whenever that cannot be established."""
    installation = await ProjectGitInstallationRepository(session).get_by_project(
        project_id
    )
    if installation is None or installation.repository_id is None:
        return False
    try:
        found = await repository_named(installation.installation_id, name)
    except (GitHubAppError, httpx.HTTPError):
        return False
    return found is not None and found[0] == installation.repository_id


async def tokens_for_project(project_id: uuid.UUID, session: AsyncSession):
    binding = await binding_for_project(project_id, session)
    if binding is None:
        return None
    if binding.kind == "github_app":
        return await github_app_tokens_for_project(project_id, session)
    if binding.kind == "forgejo":
        return ForgejoTokens(
            binding, sessions=async_sessionmaker(session.bind, expire_on_commit=False)
        )
    raise GatewayUnavailableError(say("forgeKindUnknown"))


# A GitHub App installation has ONE hourly REST quota for everything the
# platform does with a repository: the background pollers and sweeps, and a
# person delivering or merging a card. Background work stops while less than
# this share is left, so a burst of polling cannot use up what a person's
# request needs; it resumes on a later tick, after the hourly reset at worst.
KEPT_FOR_PEOPLE = 0.2


async def background_quota(
    project_id: uuid.UUID, session: AsyncSession
) -> GitHubAppTokens | None:
    """The installation whose shared quota background work for this project
    spends, or None when there is none to protect (only a GitHub App
    installation has one).

    Database reads only. Asking GitHub how much is left is a separate step
    (`quota_serves_background`), so a caller can let go of its connection,
    and of any row it holds, before that request is in flight.
    """
    binding = await binding_for_project(project_id, session)
    if binding is None or binding.kind != "github_app":
        return None
    return await github_app_tokens_for_project(project_id, session)


async def quota_serves_background(quota: GitHubAppTokens | None) -> bool:
    """Whether background work may spend `quota` now; True when there is no
    shared quota to protect."""
    return quota is None or await installation_serves_background(quota)


def installation_known_closed(tokens: GitHubAppTokens) -> bool:
    """What this process already knows without asking GitHub: closed while its
    last word on the installation was a refusal that has not expired, or while
    its last report put the quota under the share kept for people."""
    installation = tokens.installation_id
    if forge_quota.refused_until(installation) is not None:
        return True
    seen = forge_quota.reading(installation)
    return seen is not None and seen.remaining < seen.limit * KEPT_FOR_PEOPLE


async def installation_serves_background(tokens: GitHubAppTokens) -> bool:
    """Whether background work may spend this installation's quota now.

    No while GitHub's last word on the installation was a quota refusal and the
    time it named has not come, and no while its last report put the quota
    under the share kept for people and the hour has not reset: in both cases
    GitHub has already answered, so asking again would only spend a request on
    hearing it twice. Otherwise `GET /rate_limit` (which does not count against
    the quota) says how much is left. When GitHub cannot say, the work goes
    ahead and meets whatever GitHub answers it with — and that answer, if it
    is a refusal, closes the installation for the next caller.
    """
    installation = tokens.installation_id
    if installation_known_closed(tokens):
        return False
    try:
        quota = await tokens.core_quota()
    except (GitHubAppError, httpx.HTTPError):
        return True
    if forge_quota.refused_until(installation) is not None:
        return False
    if quota is None:
        return True
    remaining, limit = quota
    return remaining >= limit * KEPT_FOR_PEOPLE


@dataclass(frozen=True)
class RepoReadToken:
    """A read token for one repository's own CI, or why there is none."""

    token: str | None
    reason: str


async def github_read_token_for_repo(repo: str, session: AsyncSession) -> RepoReadToken:
    """A read token for ``repo``'s own CI, or the reason there is none.

    棘轮的采集按**仓库名**找人：它手上没有项目 id（那份快照说的是仓库的 CI），而
    要读的正是这个仓库的工件。所以这里多一个按仓库查的入口，而不是让采集自己去摸
    `project.repositories` —— 「哪个仓库归哪个项目、拿什么凭据」是这一块的事，别处
    只该问这一句。按仓库查这一步复用 `ProjectGitInstallationRepository.get_by_repo`
    （仓库名大小写不敏感的那条既有查法），不另写一遍。

    铸牌子也在这里做完，理由相同：App 拒签（403、安装信息读不到）和**网络本身不通**
    都是这一块的故障。调用方要的是「没有凭据，原因是这句」；让异常穿出去，采集那侧
    只剩一个 500 —— 页面上已存下的点还在，却没人知道这次为什么没有新的。

    两种失败分开写，因为它们是两件事：`GitHubAppError` 是对方答复了、拒绝了；httpx
    的 `HTTPError`（ConnectError、TimeoutException 都是它的子类）是我们根本没问到话。
    合成一句会让人以为 GitHub 说了什么。
    """
    installation = await ProjectGitInstallationRepository(session).get_by_repo(repo)
    if installation is None:
        return RepoReadToken(
            None, f"{repo} 没有可用的 GitHub App 安装（没连接，或平台 App 未配置）"
        )
    tokens = await github_app_tokens_for_project(installation.project_id, session)
    if tokens is None:
        return RepoReadToken(None, f"{repo} 所属的项目没有可用的 GitHub App 凭据")
    try:
        token, _expires = await tokens.installation_token()
    except httpx.HTTPError as exc:
        return RepoReadToken(None, f"连不上 GitHub，没能为 {repo} 取到凭据：{exc}")
    except GitHubAppError as exc:
        return RepoReadToken(None, f"GitHub 拒绝为 {repo} 签发凭据：{exc}")
    return RepoReadToken(token, "")


async def ensure_author_email(
    project_id: uuid.UUID,
    session: AsyncSession,
    email: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Associate an agent's commits with the project's Forgejo PR account."""
    binding = await binding_for_project(project_id, session)
    if binding is None or binding.kind != "forgejo":
        return
    if not binding.account_password:
        raise GatewayUnavailableError(say("forgeCredentialsMissing"))
    endpoint = binding.api_url.rstrip("/") + "/user/emails"
    auth = (binding.repo.split("/", 1)[0], forge_password(binding))
    async with forge_client(transport=transport, timeout=30, auth=auth) as client:
        response = await client.get(endpoint)
        if response.status_code != 200:
            raise GatewayUnavailableError(say("forgeAuthorEmailUnreadable"))
        emails = response.json()
        if not any(row["email"] == email for row in emails):
            response = await client.post(endpoint, json={"emails": [email]})
            if response.status_code == 422:
                # Concurrent tasks can register the same project agent.
                response = await client.get(endpoint)
            if response.status_code not in (200, 201):
                raise GatewayUnavailableError(say("forgeAuthorEmailUnregistered"))
            emails = response.json()
        if not any(row["email"] == email and row["verified"] for row in emails):
            raise GatewayUnavailableError(say("forgeAuthorEmailUnverified"))


async def proposal_client(project_id: uuid.UUID, session: AsyncSession):
    binding = await binding_for_project(project_id, session)
    if binding is None:
        return None
    tokens = await tokens_for_project(project_id, session)
    if tokens is None:
        raise GatewayUnavailableError(say("forgeCredentialsMissing"))
    owner, repo = binding.repo.split("/", 1)
    if binding.kind == "github_app":
        return GitHubPRClient(
            owner, repo, cast(GitHubAppTokens, tokens), api_base=binding.api_url
        )
    return ForgejoPRClient(owner, repo, tokens, api_base=binding.api_url)


async def status_client(project_id: uuid.UUID, session: AsyncSession):
    binding = await binding_for_project(project_id, session)
    if binding is None:
        raise GatewayUnavailableError(say("forgeNoRepository"))
    if binding.kind == "github_app":
        return default_client()
    if binding.kind == "forgejo":
        return ForgejoClient(binding.api_url)
    raise GatewayUnavailableError(say("forgeKindUnknown"))


async def repository_data(
    project_id: uuid.UUID,
    session: AsyncSession,
    path: str = "",
    *,
    diff: bool = False,
    release_session: bool = False,
) -> Any:
    binding = await binding_for_project(project_id, session)
    tokens = await tokens_for_project(project_id, session)
    if binding is None or tokens is None:
        raise GatewayUnavailableError(say("forgeRepositoryUnavailable"))
    if release_session:
        await release_read_session(session)
    token, _ = await tokens.installation_token()
    try:
        async with forge_client(timeout=30) as client:
            response = await client.get(
                f"{binding.api_url.rstrip('/')}/repos/{binding.repo}{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github.diff"
                    if diff
                    else "application/json",
                },
            )
    except httpx.TimeoutException as exc:
        raise GatewayUnavailableError(say("forgeTimeout")) from exc
    except httpx.RequestError as exc:
        raise GatewayUnavailableError(say("forgeUnreachable")) from exc
    if response.status_code == 404:
        return None
    if forge_quota.rate_limited(response):
        raise ForgeRateLimitedError(_rate_limit_message(response))
    if response.is_error:
        raise GatewayUnavailableError(
            say("forgeReadFailed", status=response.status_code)
        )
    return response.text if diff else response.json()


class ForgeRateLimitedError(GatewayUnavailableError):
    """The forge's API quota for this installation is used up for now.

    Every call the platform makes for a repo — the pollers and each person's
    delivery alike — draws on the one hourly quota of the App installation,
    so this is a state that clears by itself at the reset, not a refusal.
    """

    retryable = True


def _rate_limit_message(response: httpx.Response) -> str:
    wait_s = forge_quota.wait_seconds(response)
    wait = (
        say("forgeQuotaBackSoon")
        if wait_s is None
        else say("forgeQuotaBackInMinutes", minutes=max(1, math.ceil(wait_s / 60)))
    )
    return say("forgeQuotaExhausted", wait=wait)


async def branch_head(
    project_id: uuid.UUID,
    session: AsyncSession,
    branch: str,
    *,
    release_session: bool = False,
):
    data = await repository_data(
        project_id,
        session,
        f"/branches/{quote(branch, safe='')}",
        release_session=release_session,
    )
    if data is None:
        return None
    binding = await binding_for_project(project_id, session)
    if binding is None:
        raise GatewayUnavailableError(say("forgeNoRepository"))
    return data["commit"]["id" if binding.kind == "forgejo" else "sha"]


async def default_branch(project_id: uuid.UUID, session: AsyncSession) -> str:
    binding = await binding_for_project(project_id, session)
    if binding is None:
        raise GatewayUnavailableError(say("forgeNoRepository"))
    data = await repository_data(project_id, session)
    if not data or not data.get("default_branch"):
        raise GatewayUnavailableError(say("forgeNoDefaultBranch"))
    binding.default_branch = data["default_branch"]
    await session.flush()
    return binding.default_branch


_ACCOUNT_PREFIX = "cheese-"
_PROJECT_ACCOUNT = re.compile(r"cheese-([0-9a-f]{32})")
# Longer than any creation can still be running: its project row is invisible
# to other sessions until it commits, after the forge calls return or time out.
_ORPHAN_GRACE = timedelta(hours=1)


class ForgeUnreachableError(GatewayUnavailableError):
    """The project forge did not answer in time, or could not be reached.

    A slow forge is a state that passes; the same request succeeds later.
    """

    retryable = True


async def provision_repository(
    project_id: uuid.UUID,
    session: AsyncSession,
    *,
    initialize: bool = True,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ProjectForge:
    try:
        return await _provision_repository(
            project_id, session, initialize=initialize, transport=transport
        )
    except httpx.TimeoutException as exc:
        raise ForgeUnreachableError(say("forgeServiceTimeout")) from exc
    except httpx.RequestError as exc:
        raise ForgeUnreachableError(say("forgeServiceUnreachable")) from exc


async def _provision_repository(
    project_id: uuid.UUID,
    session: AsyncSession,
    *,
    initialize: bool,
    transport: httpx.AsyncBaseTransport | None,
) -> ProjectForge:
    existing = await binding_for_project(project_id, session)
    if existing is not None:
        await ensure_repository_webhook(existing, transport=transport)
        return existing
    project = await session.get(Project, project_id)
    if project and (project.settings or {}).get("forge_kind") == "github_app":
        raise GatewayUnavailableError(say("forgeConnectGithubFirst"))
    if not settings.forgejo_url or not settings.forgejo_admin_token:
        raise GatewayUnavailableError(say("forgeHostingNotConfigured"))
    public = settings.forgejo_url.rstrip("/")
    api = (settings.forgejo_api_url or public + "/api/v1").rstrip("/")
    username = _ACCOUNT_PREFIX + project_id.hex
    # Derived from the project id, so a creation retried with the same id (the
    # client keeps it across retries) adopts the account and repository an
    # earlier attempt left behind when its transaction rolled back.
    password = (
        hmac.new(
            settings.jwt_secret.encode(),
            f"forgejo-project:{project_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        + "aA1!"
    )
    async with forge_client(transport=transport, timeout=30) as client:
        response = await client.post(
            api + "/admin/users",
            headers={"Authorization": "token " + settings.forgejo_admin_token},
            json={
                "username": username,
                "email": username + "@users.invalid",
                "password": password,
                "must_change_password": False,
                "send_notify": False,
                "visibility": "private",
            },
        )
        if response.status_code not in (201, 422):
            raise GatewayUnavailableError(
                say("forgejoAccountCreateFailed", status=response.status_code)
            )
        # Authenticate as that account, even after 422; never adopt an unknown owner.
        auth = (username, password)
        repo_url = api + f"/repos/{username}/project"
        response = await client.get(repo_url, auth=auth)
        if response.status_code == 404:
            response = await client.post(
                api + "/user/repos",
                auth=auth,
                json={
                    "name": "project",
                    "private": True,
                    "auto_init": initialize,
                    "default_branch": "main",
                },
            )
            if response.status_code == 409:
                # An earlier attempt's creation finished between the two calls.
                response = await client.get(repo_url, auth=auth)
        if response.status_code not in (200, 201):
            raise GatewayUnavailableError(
                say("forgejoRepoCreateFailed", status=response.status_code)
            )
        data = response.json()
    binding = ProjectForge(
        project_id=project_id,
        kind="forgejo",
        repo=f"{username}/project",
        url=f"{public}/{username}/project.git",
        api_url=api,
        default_branch=data.get("default_branch") or "main",
        account_password=seal_forge_password(project_id, password),
    )
    session.add(binding)
    await session.flush()
    await ensure_repository_webhook(binding, transport=transport)
    return binding


async def sweep_orphan_accounts(
    sessions: SessionFactory, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, int]:
    """Delete forge accounts whose project creation never committed.

    A creation that fails after Forgejo made the account rolls back its project
    row, and nothing else refers to the account; one the person never retried
    stays behind with its repository. Accounts the platform does not name after
    a project (the platform's own, for one) are never touched.
    """
    counts = {"deleted": 0, "failed": 0}
    if not settings.forgejo_url or not settings.forgejo_admin_token:
        return counts
    api = (
        settings.forgejo_api_url or settings.forgejo_url.rstrip("/") + "/api/v1"
    ).rstrip("/")
    headers = {"Authorization": "token " + settings.forgejo_admin_token}
    cutoff = datetime.now(UTC) - _ORPHAN_GRACE
    async with forge_client(transport=transport, timeout=30, headers=headers) as client:
        candidates: dict[uuid.UUID, str] = {}
        page = 1
        while True:
            response = await client.get(
                api + "/admin/users", params={"page": page, "limit": 50}
            )
            response.raise_for_status()
            accounts = response.json()
            for account in accounts:
                match = _PROJECT_ACCOUNT.fullmatch(account["login"])
                created = datetime.fromisoformat(account["created"])
                if match and created < cutoff:
                    candidates[uuid.UUID(match[1])] = account["login"]
            if len(accounts) < 50:
                break
            page += 1
        if not candidates:
            return counts
        async with sessions() as session:
            live = set(
                await session.scalars(
                    select(Project.id).where(Project.id.in_(candidates))
                )
            )
        for project_id, login in candidates.items():
            if project_id in live:
                continue
            response = await client.delete(
                api + f"/admin/users/{login}", params={"purge": "true"}
            )
            if response.status_code in (204, 404):
                counts["deleted"] += 1
                logging.getLogger(__name__).info(
                    "Deleted forge account %s: its project was never created", login
                )
            else:
                counts["failed"] += 1
                logging.getLogger(__name__).warning(
                    "Could not delete forge account %s (HTTP %s)",
                    login,
                    response.status_code,
                )
    return counts


async def ensure_repository_webhook(
    binding: ProjectForge, *, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    """Repair the platform's subscription, including repositories created earlier."""
    if (
        binding.kind != "forgejo"
        or not settings.forge_webhook_url
        or not settings.forge_event_secret
    ):
        return
    endpoint = f"{binding.api_url.rstrip('/')}/repos/{binding.repo}/hooks"
    hook_url = settings.forge_webhook_url.rstrip("/") + f"/{binding.project_id}"
    wanted = {
        "type": "forgejo",
        "active": True,
        "events": [
            "push",
            "pull_request",
            "action_run_failure",
            "action_run_recover",
            "action_run_success",
        ],
        "config": {
            "url": hook_url,
            "content_type": "json",
            "secret": project_secret(settings.forge_event_secret, binding.project_id),
        },
    }
    if binding.account_password is None:
        raise GatewayUnavailableError(say("forgeCredentialsMissing"))
    auth = (binding.repo.split("/", 1)[0], forge_password(binding))
    async with forge_client(transport=transport, timeout=30, auth=auth) as client:
        matching = []
        page = 1
        while True:
            response = await client.get(endpoint, params={"page": page, "limit": 50})
            response.raise_for_status()
            hooks = response.json()
            matching.extend(
                hook for hook in hooks if hook.get("config", {}).get("url") == hook_url
            )
            if len(hooks) < 50:
                break
            page += 1
        complete = next(
            (
                hook
                for hook in matching
                if hook.get("type") == "forgejo"
                and set(wanted["events"]) <= set(hook["events"])
            ),
            None,
        )
        if complete:
            # PATCH refreshes active state and the signing key after rotation.
            response = await client.patch(f"{endpoint}/{complete['id']}", json=wanted)
        else:
            # Forgejo 15's PATCH ignores Actions event fields. Create the full
            # subscription before removing its old version so delivery stays on.
            response = await client.post(endpoint, json=wanted)
        response.raise_for_status()
        retained_id = response.json()["id"]
        for old in matching:
            if old["id"] != retained_id:
                response = await client.delete(f"{endpoint}/{old['id']}")
                if response.status_code != 404:
                    response.raise_for_status()


async def reconcile_repository_webhooks(sessions: SessionFactory) -> dict[str, int]:
    counts = {"configured": 0, "failed": 0}
    if not settings.forge_webhook_url or not settings.forge_event_secret:
        return counts
    async with sessions() as session:
        bindings = list(
            await session.scalars(
                select(ProjectForge).where(ProjectForge.kind == "forgejo")
            )
        )
    for binding in bindings:
        try:
            await ensure_repository_webhook(binding)
            counts["configured"] += 1
        except Exception:
            counts["failed"] += 1
            logging.getLogger(__name__).exception(
                "Repository event configuration failed for project %s",
                binding.project_id,
            )
    return counts
