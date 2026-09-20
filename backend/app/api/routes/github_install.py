"""Connect projects through a manager's verified GitHub App installation access.

The setup URL's installation_id is caller-controlled. The signed state identifies
who started the flow; GitHub's user-token API proves which repos they can connect.
"""

import asyncio
import logging
import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import (
    AuthenticationRequiredError,
    ConflictError,
    ForbiddenError,
    GatewayUnavailableError,
    InternalServerError,
)
from app.core.github_install_state import (
    INSTALL_TTL_S,
    mint_install_state,
    verify_install_state,
)
from app.core.single_use_state import SingleUseUnavailableError, claim, reserve
from app.domain.agent.github_app import (
    GitHubAppError,
    fetch_user_installation_repos,
    list_user_installations,
)
from app.domain.identity.actor import Actor
from app.domain.membership.services import MemberService
from app.domain.oauth.repositories import OAuthConnectionRepository
from app.domain.oauth.services import OAuthService
from app.domain.project.repositories import (
    ProjectGitInstallationRepository,
    ProjectRepository,
)
from app.domain.project.services import ProjectService
from app.domain.review.github_pr import parse_github_repo
from app.domain.user.repositories import UserRepository
from app.domain.workspace import service as ws

logger = logging.getLogger(__name__)
router = APIRouter(tags=["github"])
_INSTALL_SCOPE = "github_install"
_SETTINGS_ROUTE = "/projects/{project_id}/settings"


def _settings_redirect(project_id: uuid.UUID | None, **query: str) -> RedirectResponse:
    path = _SETTINGS_ROUTE.format(project_id=project_id) if project_id else "/"
    url = f"{settings.frontend_url}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    return RedirectResponse(url, status_code=302)


async def _manager(
    project_id: uuid.UUID, resolver: ActorResolverDep, db: AsyncSession
) -> Actor:
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    if not actor.authenticated:
        raise AuthenticationRequiredError("需要登录才能连接 GitHub 仓库")
    await ProjectService(db).get_or_404(project_id)
    try:
        await MemberService(db).require_manager(project_id, actor)
    except ForbiddenError:
        raise ForbiddenError("只有项目 owner / lead 能连接 GitHub 仓库") from None
    return actor


async def _user_token(db: AsyncSession, actor: Actor) -> tuple[int, str]:
    user = await UserRepository(db).get_by_username(actor.handle)
    token = (
        await OAuthService(OAuthConnectionRepository(db)).get_github_user_token(user.id)
        if user
        else None
    )
    if user is None or token is None:
        raise ForbiddenError("请先在项目设置中连接或重新连接 GitHub 账号，再连接仓库")
    return user.id, token


async def _install_url(project_id: uuid.UUID, actor: Actor, user_id: int) -> str:
    state = mint_install_state(project_id, user_id=user_id, handle=actor.handle)
    claims = verify_install_state(state)
    assert claims is not None
    try:
        await reserve(_INSTALL_SCOPE, claims.jti, ttl_s=INSTALL_TTL_S)
    except SingleUseUnavailableError:
        raise InternalServerError("暂时无法发起 GitHub 仓库连接，请稍后重试") from None
    return f"https://github.com/apps/{settings.github_app_slug}/installations/new?state={state}"


async def _upstream_repo(project_id: uuid.UUID) -> str | None:
    upstream = await asyncio.to_thread(ws.get_upstream, project_id)
    parsed = parse_github_repo(upstream) if upstream else None
    return f"{parsed[0]}/{parsed[1]}".lower() if parsed else None


def _writable(repo: dict) -> bool:
    # A read-only collaborator must not acquire the App's write access by binding.
    permissions = repo.get("permissions") or {}
    return permissions.get("push") is True or permissions.get("admin") is True


async def _connect(
    db: AsyncSession, project_id: uuid.UUID, installation_id: int, repo: dict
) -> dict:
    await ProjectGitInstallationRepository(db).upsert(
        project_id=project_id,
        installation_id=installation_id,
        repo=repo["full_name"],
        account=repo["owner"]["login"],
    )
    return {
        "connected": True,
        "repo": repo["full_name"],
        "account": repo["owner"]["login"],
    }


@router.get("/projects/{project_id}/github/connection")
async def get_github_connection(
    project_id: uuid.UUID,
    resolver: ActorResolverDep,
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    if not actor.authenticated:
        raise AuthenticationRequiredError("需要登录才能查看 GitHub 连接")
    await ProjectService(db).get_or_404(project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    installation = await ProjectGitInstallationRepository(db).get_by_project(project_id)
    if installation is None:
        return ok({"connected": False})
    return ok(
        {"connected": True, "repo": installation.repo, "account": installation.account}
    )


@router.post("/projects/{project_id}/github/connect")
async def connect_github_repo(
    project_id: uuid.UUID,
    resolver: ActorResolverDep,
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = await _manager(project_id, resolver, db)
    existing = await ProjectGitInstallationRepository(db).get_by_project(project_id)
    if existing:
        return ok(
            {"connected": True, "repo": existing.repo, "account": existing.account}
        )
    user_id, token = await _user_token(db, actor)
    target = await _upstream_repo(project_id)
    if target:
        try:
            for installation in await list_user_installations(token):
                for repo in await fetch_user_installation_repos(
                    token, installation["id"]
                ):
                    if str(repo.get("full_name", "")).lower() == target:
                        if not _writable(repo):
                            raise ForbiddenError(
                                "连接仓库需要你的 GitHub 账号对该仓库有写入权限"
                            )
                        return ok(
                            await _connect(db, project_id, installation["id"], repo)
                        )
        except GitHubAppError as exc:
            raise GatewayUnavailableError(
                "无法验证 GitHub 仓库访问权限，请重新连接 GitHub 账号后重试"
            ) from exc
    return ok(
        {
            "connected": False,
            "install_url": await _install_url(project_id, actor, user_id),
        }
    )


@router.get("/projects/{project_id}/github/install-url")
async def get_github_install_url(
    project_id: uuid.UUID,
    resolver: ActorResolverDep,
    db: AsyncSession = Depends(get_db),
) -> dict:
    actor = await _manager(project_id, resolver, db)
    user_id, _ = await _user_token(db, actor)
    return ok({"url": await _install_url(project_id, actor, user_id)})


@router.get("/github/app/callback")
async def github_app_install_callback(
    installation_id: int | None = Query(default=None),
    setup_action: str | None = Query(default=None),
    state: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    claims = verify_install_state(state) if state else None
    if claims is None:
        return _settings_redirect(None, github_install="error", reason="invalid_state")
    project_id = claims.project_id

    def failure(reason: str) -> RedirectResponse:
        return _settings_redirect(project_id, github_install="error", reason=reason)

    try:
        if not await claim(_INSTALL_SCOPE, claims.jti):
            return _settings_redirect(
                None, github_install="error", reason="invalid_state"
            )
        project = await ProjectRepository(db).get(project_id)
        if project is None:
            return failure("project_not_found")
        user = await UserRepository(db).get_by_id(claims.user_id)
        if user is None or user.username != claims.handle:
            return failure("access_denied")
        actor = Actor(
            handle=user.username, user_id=user.id, is_agent=False, via="token"
        )
        await MemberService(db).require_manager(project_id, actor)
        if setup_action == "request":
            return _settings_redirect(project_id, github_install="pending")
        if not installation_id:
            return failure("missing_installation_id")
        _, token = await _user_token(db, actor)
        # A setup URL is not proof that this installation belongs to the caller.
        repos = await fetch_user_installation_repos(token, installation_id)
        if not repos:
            return failure("no_accessible_repos")
        target = await _upstream_repo(project_id)
        if target:
            repo = next(
                (r for r in repos if str(r.get("full_name", "")).lower() == target),
                None,
            )
            if repo is None:
                return failure("upstream_not_accessible")
        elif len(repos) == 1:
            repo = repos[0]
        else:
            return failure("repository_selection_required")
        if not _writable(repo):
            return failure("repository_write_required")
        await _connect(db, project_id, installation_id, repo)
    except ForbiddenError:
        return failure("access_denied")
    except ConflictError:
        await db.rollback()
        return failure("installation_conflict")
    except GitHubAppError:
        return failure("github_error")
    except SingleUseUnavailableError:
        return failure("internal_error")
    except Exception:
        await db.rollback()
        logger.exception("github install callback failed for project %s", project_id)
        return failure("internal_error")
    return _settings_redirect(
        project_id, github_install="success", repo=repo["full_name"]
    )
