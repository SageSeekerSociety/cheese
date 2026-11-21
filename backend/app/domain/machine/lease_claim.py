"""The claim an environment carries while its executor is installed.

Taking hands in an environment claims it (`session_work._attempt`): while the
claim stands, every other attempt there — a session's, another session's, a
person's — waits for the installation holding it instead of starting a second
one beside it. It ends when the installation does — handed over as ready, or
lapsed on failure — and, when the backend process running the installation is
gone, by not being renewed.
"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.environment.models import Environment

logger = logging.getLogger(__name__)

# A claim on an environment stands this long past the last word from the
# installation holding it, which renews it every CLAIM_RENEW_S while it runs
# (`kept`). An installation whose backend process is gone — a deploy's
# restart, a crash, an OOM kill — stops renewing, and its claim lapses within
# this long; one still running in another backend, as during a rollout's
# overlap, keeps it, since every backend reads the same row. Three missed
# renewals, so a slow beat does not hand a live installation's claim away.
CLAIM_TTL_S = 30.0
CLAIM_RENEW_S = 10.0
# The longest an installation of a session's executor may take before the
# machine it runs on is given up on: the deadline of the install command
# itself, and of the in-place preparation beside it (`session_work`). One
# installation burning a whole tool call is what made a hung host look like a
# machine that never finishes preparing: the call that waited on it was the
# last the session had. Kept well inside the 660 s one tool call has in full,
# so the attempt fails and the session is placed again while its caller still
# has time.
INSTALL_MOST_S = 120.0
# The calls into the machine one installation makes one after another: the ping
# and the prepare that decide how to start the executor, the install itself, and
# the ping that asks the executor it installed to answer (`session_work._install`).
# Each is bounded by INSTALL_MOST_S, and they run in sequence under one claim.
CALLS_IN_AN_INSTALL = 3
# The longest one installation holds the claim, renewed or not: every call it
# makes in sequence, and one TTL past the last of them, so the claim cannot
# lapse while the installation that took it is still running. One TTL past the
# install command alone was shorter than an installation that spends its whole
# deadline on the install and then waits on the executor: the claim lapsed with
# the installation still going, a second request read the lease as free and
# started a second installation beside it on the same machine — the one thing
# this claim exists to prevent.
CLAIM_MOST_S = CALLS_IN_AN_INSTALL * INSTALL_MOST_S + CLAIM_TTL_S


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
async def kept(
    bind, environment_id, claim: str, *, since: datetime, most_s: float = CLAIM_MOST_S
):
    """Keep ``claim`` on the environment standing while the work inside runs,
    and stop renewing it the moment that work ends, however it ends.

    ``most_s`` includes the longer install budget when restoring a root backup.
    The renewal is the only thing that says the installation is alive: when
    this process exits it goes with it, and the claim lapses unrenewed within
    ``CLAIM_TTL_S``. The writes that end the claim come after this exits, so a
    renewal can never land after them and stand the claim up again."""
    renewing = asyncio.ensure_future(_renew(bind, environment_id, claim, since, most_s))
    try:
        yield
    finally:
        renewing.cancel()
        with suppress(asyncio.CancelledError):
            await renewing


async def _renew(
    bind, environment_id, claim: str, since: datetime, most_s: float
) -> None:
    most = since + timedelta(seconds=most_s)
    while True:
        await asyncio.sleep(CLAIM_RENEW_S)
        until = min(datetime.now(UTC) + timedelta(seconds=CLAIM_TTL_S), most)
        try:
            async with AsyncSession(bind, expire_on_commit=False) as db:
                renewed = await db.execute(
                    update(Environment)
                    .where(Environment.id == environment_id, Environment.claim == claim)
                    .values(claim_until=until)
                )
                await db.commit()
        except Exception:  # the next beat tries again
            # One missed beat is not the installation's death; a database that
            # stays away for the whole TTL lets the claim lapse, as it should.
            logger.warning(
                "could not renew the claim of environment %s", environment_id
            )
            continue
        if not renewed.rowcount:  # type: ignore[attr-defined]
            # Handed over, lapsed, or taken by a later attempt: the claim is
            # no longer this installation's to keep.
            return
        if until >= most:
            return


async def moved(db: AsyncSession, environment_id, claim) -> bool:
    """Another request holds the installation in this environment; it has
    moved on once its claim is gone or has lapsed."""
    found = (
        await db.execute(
            select(Environment.claim, Environment.claim_until).where(
                Environment.id == environment_id
            )
        )
    ).first()
    if found is None or found.claim != claim:
        return True
    until = found.claim_until
    return until is None or until <= datetime.now(UTC)
