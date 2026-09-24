"""When to offer a passkey to someone who has just signed in without one.

The offer is one screen after sign-in, and declining it has to be respected
rather than worn down: the first "later" holds it back for 30 days, the second
for 90, and the third ends it. From the second showing on the person may also
end it at once. Adding a passkey, from anywhere, ends it too.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.passkey.models import PasskeyCredential, PasskeyPrompt

# How long each "later" holds the offer back, in order. One more "later" than
# there are entries ends the offer.
SNOOZES = (timedelta(days=30), timedelta(days=90))


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class PromptState:
    # Whether to show the offer now.
    due: bool
    # Whether the offer may also say "don't ask again": it has been declined
    # before, so this is at least its second showing.
    can_stop_asking: bool


class PasskeyPromptService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def state(self, user_id: int) -> PromptState:
        row = await self._session.get(PasskeyPrompt, user_id)
        return PromptState(
            due=await self._due(user_id, row),
            can_stop_asking=row is not None and row.dismissals > 0,
        )

    async def dismiss(self, user_id: int, *, forever: bool = False) -> None:
        """Record one answer to the offer that is showing.

        Only an offer that is due can be declined: a second request for the
        same showing (a double click, a retried request) finds it no longer
        due and changes nothing, instead of counting as the next "later".
        """
        row = await self._locked_row(user_id)
        if not await self._due(user_id, row):
            return
        row.dismissals += 1
        if forever or row.dismissals > len(SNOOZES):
            row.ended = True
            row.snoozed_until = None
        else:
            row.snoozed_until = _utcnow() + SNOOZES[row.dismissals - 1]
        await self._session.flush()

    async def end(self, user_id: int) -> None:
        row = await self._locked_row(user_id)
        row.ended = True
        row.snoozed_until = None
        await self._session.flush()

    async def _due(self, user_id: int, row: PasskeyPrompt | None) -> bool:
        if row is not None and (
            row.ended or (row.snoozed_until and row.snoozed_until > _utcnow())
        ):
            return False
        passkeys = await self._session.scalar(
            select(func.count())
            .select_from(PasskeyCredential)
            .where(PasskeyCredential.user_id == user_id)
        )
        return not passkeys

    async def _locked_row(self, user_id: int) -> PasskeyPrompt:
        # Created if absent and locked, so two answers arriving together are
        # counted one after the other rather than both from the same start.
        await self._session.execute(
            insert(PasskeyPrompt)
            .values(user_id=user_id, dismissals=0, ended=False)
            .on_conflict_do_nothing(index_elements=[PasskeyPrompt.user_id])
        )
        row = await self._session.scalar(
            select(PasskeyPrompt)
            .where(PasskeyPrompt.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert row is not None
        return row
