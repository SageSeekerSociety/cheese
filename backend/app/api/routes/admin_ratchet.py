"""棘轮: the architecture ratchet's series, and the button that pulls a newer one.

One read endpoint, one action. The read is what the page draws; it reads the
platform's own table and never GitHub, so opening the page is cheap and cannot
be the thing that gets rate-limited. The action is 「刷新」: pull the artifacts
now instead of waiting for the periodic clock.

Both are behind the same gate as the rest of `/admin` (`PlatformAdminDep`): this
reports how the platform's own code is doing, alongside which files are over
budget and which imports cross a boundary. Nothing here is a per-project number.

The response is the page's whole state — no second call for 「现在是不是在还债」
— because the pieces only mean anything together: a direction is only readable
next to the fingerprint segment it was measured in, and a count is only
readable next to whether it was measured at all.
"""

from fastapi import APIRouter

from app.api.response import ok
from app.api.routes.admin_common import DbSession, PlatformAdminDep
from app.core.config import settings
from app.domain.ratchet.board import build_board
from app.domain.ratchet.ingest import ingest_once
from app.domain.ratchet.store import RatchetSnapshots

router = APIRouter(prefix="/admin/ratchet", tags=["admin"])


async def _current_board(db) -> dict:
    store = RatchetSnapshots(db)
    repo = settings.ratchet_repository
    rows = await store.newest(repo, settings.ratchet_series_points)
    return build_board(
        rows,
        repo=repo,
        deployed_commit=settings.released_commit,
        total_stored=await store.count(repo),
    )


@router.get("")
async def ratchet_board(db: DbSession, handle: PlatformAdminDep) -> dict:
    """The series as stored, newest collection included. Never touches GitHub."""
    return ok(await _current_board(db))


@router.post("/refresh")
async def ratchet_refresh(db: DbSession, handle: PlatformAdminDep) -> dict:
    """Pull new artifacts now, then answer with the board the pull produced.

    Deliberately synchronous and not a background kick: 「刷新」 is a person
    asking whether the merge they just watched landed in the series, and an
    answer that says 「稍后再看」 is not an answer. It reads at most
    `DEFAULT_ARTIFACT_LIMIT` artifacts and stores only the runs it does not
    already have, so the second press is a listing call and nothing else.
    """
    report = await ingest_once(db)
    board = await _current_board(db)
    board["refresh"] = report
    return ok(board)
