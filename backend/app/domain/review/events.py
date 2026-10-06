"""Keep a deployment's outbound event connection alive across relay restarts."""

import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed
from websockets.frames import CloseCode

from app.core.config import settings
from app.core.db import SessionFactory
from app.core.forge_events import subscription_assertion
from app.domain.project.models import ProjectGitInstallation
from app.domain.review import pr_poll

logger = logging.getLogger(__name__)

RECONNECT_DELAY_S = 10


async def register_subscriptions(socket, sessions: SessionFactory):
    if (
        not settings.github_app_id
        or not settings.github_app_private_key_path
        or settings.forge_event_github_app_id != settings.github_app_id
    ):
        return
    deployment = (
        urlsplit(settings.forge_event_relay_url).path.rstrip("/").split("/")[-2]
    )
    private_key = Path(settings.github_app_private_key_path).read_text()
    while True:
        async with sessions() as session:
            rows = list(
                (
                    await session.execute(
                        select(
                            ProjectGitInstallation.installation_id,
                            ProjectGitInstallation.repo,
                        )
                    )
                ).all()
            )
        assertion = subscription_assertion(
            app_id=settings.github_app_id,
            private_key=private_key,
            deployment=deployment,
            repositories=[(row[0], row[1]) for row in rows],
        )
        await socket.send(
            json.dumps({"kind": "github_subscriptions", "assertion": assertion})
        )
        await asyncio.sleep(60)


#: The key of a refresh of every project, run after each (re)connect.
_EVERYTHING = ("*",)


class _Refreshes:
    """Forge refreshes still to run, at most one waiting per repository.

    A relayed event says only that a repository changed, and one refresh of
    that repository answers every event about it that arrived before the
    refresh started. A busy repository sends several events a minute (pushes,
    check runs, reviews), and refreshing it once per event made the platform
    sweep it back to back, spending the GitHub App installation's hourly quota
    on answers it already had.

    The refreshes also run beside the socket, not inside its read loop: a read
    loop waiting on a minute-long sweep stops reading, the relay's keepalive
    times out, and every reconnect starts another refresh of everything.
    """

    def __init__(self, chat) -> None:  # noqa: ANN001
        self._chat = chat
        self._waiting: dict[tuple, dict] = {}
        self._wake = asyncio.Event()

    def everything(self) -> None:
        self._waiting[_EVERYTHING] = {}
        self._wake.set()

    def changed(self, event: dict) -> None:
        key = (
            event.get("kind"),
            str(event.get("repo", "")).lower(),
            event.get("project_id"),
        )
        self._waiting[key] = event
        self._wake.set()

    async def run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._waiting:
                if _EVERYTHING in self._waiting:
                    # Covers every repository that changed before it starts.
                    self._waiting.clear()
                    await self._refresh(None)
                    continue
                key = next(iter(self._waiting))
                await self._refresh(self._waiting.pop(key))

    async def _refresh(self, event: dict | None) -> None:
        try:
            if event is None:
                await pr_poll.open_draft_prs(self._chat)
                await pr_poll.poll_open_prs(self._chat)
            else:
                await pr_poll.forge_repository_changed(self._chat, **event)
        except Exception:  # noqa: BLE001 — the next event or tick retries
            logger.warning("Forge refresh failed for %s", event, exc_info=True)


async def listen(chat, sessions: SessionFactory):
    refreshes = _Refreshes(chat)
    worker = asyncio.create_task(refreshes.run())
    try:
        await _listen(refreshes, sessions)
    finally:
        worker.cancel()


def _relay_restarted(exc: BaseException) -> bool:
    """Did the relay close the socket because it is restarting (1012)? It does
    on every deploy of forge-events; that is a reconnect, not a fault."""
    if isinstance(exc, BaseExceptionGroup):
        return any(_relay_restarted(inner) for inner in exc.exceptions)
    return (
        isinstance(exc, ConnectionClosed)
        and exc.rcvd is not None
        and exc.rcvd.code == CloseCode.SERVICE_RESTART
    )


async def _listen(refreshes: _Refreshes, sessions: SessionFactory):
    while True:
        try:
            async with connect(
                settings.forge_event_relay_url,
                additional_headers={
                    "Authorization": f"Bearer {settings.forge_event_secret}"
                },
                max_size=4096,
                open_timeout=15,
            ) as socket:
                logger.info("Forge event relay connected")
                # Reconcile after every reconnect, including startup.
                refreshes.everything()
                async with asyncio.TaskGroup() as group:
                    subscription = group.create_task(
                        register_subscriptions(socket, sessions)
                    )
                    async for raw in socket:
                        event = json.loads(raw)
                        if event.get("kind") != "subscription_ack":
                            refreshes.changed(event)
                    # A clean socket close must also stop the subscription renewer.
                    subscription.cancel()
                raise ConnectionError("Forge event connection closed")
        except Exception as exc:  # noqa: BLE001 — reconnect; periodic reconciliation remains live
            if _relay_restarted(exc):
                logger.info("Forge event relay restarted; reconnecting")
            else:
                logger.warning(
                    "Forge event relay disconnected; retrying", exc_info=True
                )
            await asyncio.sleep(RECONNECT_DELAY_S)
