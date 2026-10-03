"""Charging what a gateway key spent: one way for every key the platform holds.

A model call made on one of our virtual keys is priced by the gateway, not by
whoever made it, so what it cost is read back afterwards: the key's new spend
since the checkpoint kept beside it (`gateway.drain_new_usage`), one row per
model, landed on the ledger together with the advanced checkpoint. A room's
project key, the platform's key inside a project and a person's own key are
read and charged the same way; they differ only in where the key and its
checkpoint are kept and who pays, which is what ``GatewayKey`` answers.

Exactly-once holds across concurrent reads and backend processes: the spend is
read without a lock (it is an HTTP round trip), and landed only if the
checkpoint is still the one it was read from, checked under a row lock.
"""

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent.gateway import LlmGateway, drain_new_usage
from app.domain.agent.service import AgentUsage
from app.domain.usage.credits import spend_to_credits
from app.domain.usage.ledger import Ledger, Payer

logger = logging.getLogger(__name__)


class GatewayKey(Protocol):
    """One virtual key whose spend is charged: where it and its checkpoint are
    kept, and who pays for it."""

    async def read(
        self, session: AsyncSession, *, for_update: bool = False
    ) -> tuple[str, dict | None] | None:
        """The key and its checkpoint; None when no key was ever minted.
        ``for_update`` locks the row holding the checkpoint."""
        ...

    async def advance(self, session: AsyncSession, checkpoint: dict) -> None:
        """Store the checkpoint, in the row ``read(for_update=True)`` locked."""
        ...

    async def payer(self, session: AsyncSession) -> Payer | None:
        """Who is charged; None when the platform pays (#2233)."""
        ...


@dataclass(frozen=True)
class Charge:
    """How the landed rows are labelled."""

    #: The model named on a row the gateway could not attribute to one.
    model: str
    kind: str = "chat"
    project_id: uuid.UUID | None = None
    topic_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    user_id: int | None = None


async def charge_new_spend(
    sessions: async_sessionmaker[AsyncSession],
    gateway: LlmGateway | None,
    key: GatewayKey,
    charge: Charge,
    *,
    lock: asyncio.Lock | None = None,
) -> list[AgentUsage] | None:
    """Land what ``key`` spent since its checkpoint; the rows landed, or None
    when nothing was (no new spend yet, no gateway, another read took it, or
    it failed — the next read lands it). ``lock`` is held only around the
    write, never across the gateway read."""
    return await settle(sessions, gateway, key, charge, retry_after=(), lock=lock)


async def settle(
    sessions: async_sessionmaker[AsyncSession],
    gateway: LlmGateway | None,
    key: GatewayKey,
    charge: Charge,
    *,
    retry_after: tuple[float, ...],
    lock: asyncio.Lock | None = None,
) -> list[AgentUsage] | None:
    """``charge_new_spend``, read again after each wait in ``retry_after``
    while nothing has landed: the gateway writes its spend log in batches, so
    a read right after a call often sees nothing yet. A read that fails is not
    retried; the next charge of the key lands what it missed."""
    if gateway is None:
        return None
    try:
        landed = await _charge_once(sessions, gateway, key, charge, lock)
        for delay in retry_after:
            if landed:
                break
            await asyncio.sleep(delay)
            landed = await _charge_once(sessions, gateway, key, charge, lock)
        return landed
    except Exception:  # noqa: BLE001 — charging never fails the work it follows
        logger.exception("charging gateway spend failed (%s)", charge)
        return None


async def _charge_once(
    sessions: async_sessionmaker[AsyncSession],
    gateway: LlmGateway,
    key: GatewayKey,
    charge: Charge,
    lock: asyncio.Lock | None,
) -> list[AgentUsage] | None:
    async with sessions() as session:
        found = await key.read(session)
    if found is None:
        return None
    token, checkpoint = found
    drained = await drain_new_usage(gateway, token, checkpoint)
    if drained is None or not drained[0]:
        return None
    rows, next_checkpoint = drained
    async with lock or contextlib.nullcontext():
        async with sessions() as session:
            current = await key.read(session, for_update=True)
            if current is None or current[1] != checkpoint:
                # Another read charged this window first.
                return None
            payer = await key.payer(session)
            ledger = Ledger(session)
            usages = [
                AgentUsage(
                    model=row.model,
                    input_tokens=row.prompt_tokens,
                    output_tokens=row.completion_tokens,
                    cost_usd=row.spend_usd,
                )
                for row in rows
            ]
            for usage in usages:
                # `model=""` is the one unattributed migration row
                # (`drain_new_usage`): real spend, named by the default.
                model = usage.model or charge.model
                if payer is None:
                    await ledger.record_platform(
                        kind=charge.kind,
                        model=model,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cost_usd=usage.cost_usd,
                        project_id=charge.project_id,
                        topic_id=charge.topic_id,
                        turn_id=charge.turn_id,
                    )
                    continue
                await ledger.record(
                    payer,
                    credits=spend_to_credits(usage.cost_usd),
                    model=model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_usd=usage.cost_usd,
                    route="gateway",
                    kind=charge.kind,
                    topic_id=charge.topic_id,
                    turn_id=charge.turn_id,
                    user_id=charge.user_id,
                )
            await key.advance(session, next_checkpoint)
            await session.commit()
    return usages
