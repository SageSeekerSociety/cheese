"""The claim a session's lease carries while its executor is installed.

Taking a session's hands claims its lease (`session_work._attempt`): while the
claim stands, every other attempt of that session waits for the installation
holding it instead of starting a second one beside it. It ends when the
installation does — handed over as ready, or lapsed on failure — and, when
the backend process running the installation is gone, by not being renewed.
"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.models import AgentSession
from app.domain.agent_session.services import AgentSessionService

logger = logging.getLogger(__name__)

# A claim on a session's lease stands this long past the last word from the
# installation holding it, which renews it every CLAIM_RENEW_S while it runs
# (`kept`). An installation whose backend process is gone — a deploy's
# restart, a crash, an OOM kill — stops renewing, and its claim lapses within
# this long; one still running in another backend, as during a rollout's
# overlap, keeps it, since every backend reads the same row. Three missed
# renewals, so a slow beat does not hand a live installation's claim away.
CLAIM_TTL_S = 30.0
CLAIM_RENEW_S = 10.0
# The longest one installation holds the claim, renewed or not: the install
# command's own deadline (`session_work._start_executor`).
CLAIM_MOST_S = 660.0


def still_preparing(lease: dict | None) -> bool:
    """Whether a request is installing this lease right now.

    Only a live claim says so. A lease left ``preparing`` after its install
    failed or was abandoned, or waiting on a project environment, has nobody
    finishing it; treating it as busy refused every switch of its room for
    good, the way back to a machine that works included.
    """
    until = (lease or {}).get("claim_until")
    return bool(until) and datetime.fromisoformat(until) > datetime.now(UTC)


@asynccontextmanager
async def kept(bind, session_id, claim: str, *, since: datetime):
    """Keep ``claim`` on the session's lease standing while the work inside
    runs, and stop renewing it the moment that work ends, however it ends.

    The renewal is the only thing that says the installation is alive: when
    this process exits it goes with it, and the claim lapses unrenewed within
    ``CLAIM_TTL_S``. The writes that end the claim come after this exits, so a
    renewal can never land after them and stand the claim up again."""
    renewing = asyncio.ensure_future(_renew(bind, session_id, claim, since))
    try:
        yield
    finally:
        renewing.cancel()
        with suppress(asyncio.CancelledError):
            await renewing


async def _renew(bind, session_id, claim: str, since: datetime) -> None:
    most = since + timedelta(seconds=CLAIM_MOST_S)
    while True:
        await asyncio.sleep(CLAIM_RENEW_S)
        until = min(datetime.now(UTC) + timedelta(seconds=CLAIM_TTL_S), most)
        try:
            async with AsyncSession(bind, expire_on_commit=False) as db:
                row = await AgentSessionService(db).by_id(session_id, lock=True)
                lease = (row.work_lease if row is not None else None) or {}
                if row is None or lease.get("claim") != claim:
                    # Handed over, lapsed, or taken by a later attempt: the
                    # claim is no longer this installation's to keep.
                    return
                row.work_lease = {**lease, "claim_until": until.isoformat()}
                await db.commit()
        except Exception:  # noqa: BLE001 — the next beat tries again
            # One missed beat is not the installation's death; a database that
            # stays away for the whole TTL lets the claim lapse, as it should.
            logger.warning("could not renew the claim of session %s", session_id)
            continue
        if until >= most:
            return


async def moved(db, session_id, claim) -> bool:
    """Another request of this session holds the installation; it has moved
    on once its claim is gone or has lapsed."""
    lease = await db.scalar(
        select(AgentSession.work_lease).where(AgentSession.id == session_id)
    )
    if (lease or {}).get("claim") != claim:
        return True
    until = lease.get("claim_until")
    return not until or datetime.fromisoformat(until) <= datetime.now(UTC)
