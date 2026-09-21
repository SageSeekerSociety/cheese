"""Keep a deployment's outbound event connection alive across relay restarts."""

import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from websockets.asyncio.client import connect

from app.core.config import settings
from app.core.db import SessionFactory
from app.core.forge_events import subscription_assertion
from app.domain.project.models import ProjectGitInstallation

logger = logging.getLogger(__name__)


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


async def listen(scheduler, sessions: SessionFactory):
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
                # Reconcile immediately after every reconnect, including startup.
                await scheduler.open_draft_prs()
                await scheduler.poll_open_prs()
                async with asyncio.TaskGroup() as group:
                    subscription = group.create_task(
                        register_subscriptions(socket, sessions)
                    )
                    async for raw in socket:
                        event = json.loads(raw)
                        if event.get("kind") != "subscription_ack":
                            await scheduler.forge_repository_changed(**event)
                    # A clean socket close must also stop the subscription renewer.
                    subscription.cancel()
                raise ConnectionError("Forge event connection closed")
        except Exception:  # noqa: BLE001 — reconnect; periodic reconciliation remains live
            logger.warning("Forge event relay disconnected; retrying", exc_info=True)
            await asyncio.sleep(10)
