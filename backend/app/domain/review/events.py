"""Keep a deployment's outbound event connection alive across relay restarts."""

import asyncio
import json
import logging

from websockets.asyncio.client import connect

from app.core.config import settings

logger = logging.getLogger(__name__)


async def listen(scheduler):
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
                async for raw in socket:
                    event = json.loads(raw)
                    await scheduler.forge_repository_changed(**event)
        except Exception:  # noqa: BLE001 — reconnect; periodic reconciliation remains live
            logger.warning("Forge event relay disconnected; retrying", exc_info=True)
            await asyncio.sleep(10)
