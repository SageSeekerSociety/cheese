"""Resolve the one repository and credentials a project uses."""

import hashlib
import hmac
import logging
import uuid
from typing import Any, cast
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.crypto import decrypt_text, encrypt_text
from app.core.db import SessionFactory
from app.core.errors import GatewayUnavailableError
from app.core.forge_events import project_secret
from app.domain.agent.forgejo_tokens import ForgejoTokens
from app.domain.agent.github_app import GitHubAppTokens, github_app_tokens_for_project
from app.domain.project.models import Project, ProjectForge
from app.domain.review.forgejo_pr import ForgejoClient, ForgejoPRClient
from app.domain.review.github_pr import GitHubPRClient, default_client


async def binding_for_project(
    project_id: uuid.UUID, session: AsyncSession
) -> ProjectForge | None:
    return await session.scalar(
        select(ProjectForge).where(ProjectForge.project_id == project_id)
    )


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
    raise GatewayUnavailableError("项目的代码托管类型无法识别")


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
        raise GatewayUnavailableError("项目的代码托管凭据尚未配置")
    endpoint = binding.api_url.rstrip("/") + "/user/emails"
    auth = (binding.repo.split("/", 1)[0], decrypt_text(binding.account_password))
    async with httpx.AsyncClient(transport=transport, timeout=30, auth=auth) as client:
        response = await client.get(endpoint)
        if response.status_code != 200:
            raise GatewayUnavailableError("无法读取代码托管账号的作者邮箱")
        emails = response.json()
        if not any(row["email"] == email for row in emails):
            response = await client.post(endpoint, json={"emails": [email]})
            if response.status_code == 422:
                # Concurrent tasks can register the same project agent.
                response = await client.get(endpoint)
            if response.status_code not in (200, 201):
                raise GatewayUnavailableError("无法登记代码托管账号的作者邮箱")
            emails = response.json()
        if not any(row["email"] == email and row["verified"] for row in emails):
            raise GatewayUnavailableError("代码托管服务尚未验证 agent 的作者邮箱")


async def proposal_client(project_id: uuid.UUID, session: AsyncSession):
    binding = await binding_for_project(project_id, session)
    if binding is None:
        return None
    tokens = await tokens_for_project(project_id, session)
    if tokens is None:
        raise GatewayUnavailableError("项目的代码托管凭据尚未配置")
    owner, repo = binding.repo.split("/", 1)
    if binding.kind == "github_app":
        return GitHubPRClient(
            owner, repo, cast(GitHubAppTokens, tokens), api_base=binding.api_url
        )
    return ForgejoPRClient(owner, repo, tokens, api_base=binding.api_url)


async def status_client(project_id: uuid.UUID, session: AsyncSession):
    binding = await binding_for_project(project_id, session)
    if binding is None:
        raise GatewayUnavailableError("项目没有代码仓库")
    if binding.kind == "github_app":
        return default_client()
    if binding.kind == "forgejo":
        return ForgejoClient(binding.api_url)
    raise GatewayUnavailableError("项目的代码托管类型无法识别")


async def repository_data(
    project_id: uuid.UUID,
    session: AsyncSession,
    path: str = "",
    *,
    diff: bool = False,
) -> Any:
    binding = await binding_for_project(project_id, session)
    tokens = await tokens_for_project(project_id, session)
    if binding is None or tokens is None:
        raise GatewayUnavailableError("项目的代码仓库或凭据不可用")
    token, _ = await tokens.installation_token()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{binding.api_url.rstrip('/')}/repos/{binding.repo}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.diff" if diff else "application/json",
            },
        )
    if response.status_code == 404:
        return None
    if response.is_error:
        raise GatewayUnavailableError(
            f"读取代码仓库失败（HTTP {response.status_code}）"
        )
    return response.text if diff else response.json()


async def branch_head(project_id: uuid.UUID, session: AsyncSession, branch: str):
    data = await repository_data(
        project_id, session, f"/branches/{quote(branch, safe='')}"
    )
    if data is None:
        return None
    binding = await binding_for_project(project_id, session)
    if binding is None:
        raise GatewayUnavailableError("项目没有代码仓库")
    return data["commit"]["id" if binding.kind == "forgejo" else "sha"]


async def default_branch(project_id: uuid.UUID, session: AsyncSession) -> str:
    binding = await binding_for_project(project_id, session)
    if binding is None:
        raise GatewayUnavailableError("项目没有代码仓库")
    data = await repository_data(project_id, session)
    if not data or not data.get("default_branch"):
        raise GatewayUnavailableError("代码仓库没有默认分支")
    binding.default_branch = data["default_branch"]
    await session.flush()
    return binding.default_branch


async def provision_repository(
    project_id: uuid.UUID,
    session: AsyncSession,
    *,
    initialize: bool = True,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ProjectForge:
    existing = await binding_for_project(project_id, session)
    if existing is not None:
        await ensure_repository_webhook(existing, transport=transport)
        return existing
    project = await session.get(Project, project_id)
    if project and (project.settings or {}).get("forge_kind") == "github_app":
        raise GatewayUnavailableError("请先在项目设置中连接 GitHub 仓库")
    if not settings.forgejo_url or not settings.forgejo_admin_token:
        raise GatewayUnavailableError("此部署尚未配置项目代码托管服务")
    public = settings.forgejo_url.rstrip("/")
    api = (settings.forgejo_api_url or public + "/api/v1").rstrip("/")
    username = "cheese-" + project_id.hex
    # A retried creation must recover the same account after a DB rollback.
    password = (
        hmac.new(
            settings.jwt_secret.encode(),
            f"forgejo-project:{project_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        + "aA1!"
    )
    async with httpx.AsyncClient(transport=transport, timeout=30) as client:
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
                f"Forgejo 项目账户创建失败（HTTP {response.status_code}）"
            )
        # Authenticate as that account, even after 422; never adopt an unknown owner.
        auth = (username, password)
        response = await client.get(api + f"/repos/{username}/project", auth=auth)
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
        if response.status_code not in (200, 201):
            raise GatewayUnavailableError(
                f"Forgejo 项目仓库创建失败（HTTP {response.status_code}）"
            )
        data = response.json()
    binding = ProjectForge(
        project_id=project_id,
        kind="forgejo",
        repo=f"{username}/project",
        url=f"{public}/{username}/project.git",
        api_url=api,
        default_branch=data.get("default_branch") or "main",
        account_password=encrypt_text(password),
    )
    session.add(binding)
    await session.flush()
    await ensure_repository_webhook(binding, transport=transport)
    return binding


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
        raise GatewayUnavailableError("项目的代码托管凭据尚未配置")
    auth = (binding.repo.split("/", 1)[0], decrypt_text(binding.account_password))
    async with httpx.AsyncClient(transport=transport, timeout=30, auth=auth) as client:
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
