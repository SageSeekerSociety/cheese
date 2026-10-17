"""The forge clients this service calls and the App credentials they need: the
wrapper that hands the connection back before each remote call, the status and
proposal clients, and the installation tokens for a project's repo."""

from __future__ import annotations

import uuid
from typing import cast

from app.domain.review import services as pkg
from app.domain.review.models import AcceptCard
from app.domain.review.services import poll_claim
from app.domain.review.services._shared import _GitHubCredentials
from app.domain.topic.models import Topic


async def _status_client(self: pkg.AcceptService, project_id: uuid.UUID):
    # deferred-import: tests replace this name on app.domain.review.forge_clients
    from app.domain.review.forge_clients import status_client

    return _remote(self, await status_client(project_id, self._session))


def _remote[Client](self: pkg.AcceptService, client: Client) -> Client:
    """A forge client as this service may call it: during a claimed poll,
    one that gives the database connection back before each call. The
    wrapper answers every call the client does, so it stands in as one."""
    if client is None or not self._commits_before_remote:
        return client
    return cast("Client", poll_claim.CommitsBeforeRemote(client, self._session))


async def _pr_poll_credentials(
    self: pkg.AcceptService, card: AcceptCard, topic: Topic
) -> tuple[_GitHubCredentials | None, str]:
    """The App's GitHub credentials for this card's PR — the only lane
    left (#718 deleted the personal-token one). Two mints, not one: see
    `_GitHubCredentials` for why reading checks with the write token is a
    403 that presents as a card frozen forever. Never raises — a poll
    tick degrades to "pause and retry"."""
    del card  # one lane now; the signature stays call-site-stable
    return await self._app_credentials(topic)


async def _app_credentials(
    self: pkg.AcceptService, topic: Topic
) -> tuple[_GitHubCredentials | None, str]:
    """The platform App's installation tokens for this project's repo."""
    # deferred-import: tests replace this name on app.domain.project.forge
    from app.domain.project.forge import tokens_for_project

    tokens = await tokens_for_project(topic.project_id, self._session)
    if tokens is None:
        return None, "这个项目的代码托管凭据不可用"
    tokens = _remote(self, tokens)
    try:
        write, _ = await tokens.write_token()
        read, _ = await tokens.installation_token()
    except Exception as exc:  # noqa: BLE001 — pause this tick, don't crash
        return None, f"获取代码托管凭据失败（{type(exc).__name__}）"
    return _GitHubCredentials(write=write, read=read), ""


async def _app_pr_client(self: pkg.AcceptService, topic: Topic):  # noqa: ANN202 — GitHubPRClient
    """The App-token client for this project's upstream, or None when the
    project has no GitHub side at all (no installation, or an upstream that
    is not a GitHub https remote)."""
    # deferred-import: tests replace this name on app.domain.review.forge_clients
    from app.domain.review.forge_clients import proposal_client

    return _remote(self, await proposal_client(topic.project_id, self._session))
