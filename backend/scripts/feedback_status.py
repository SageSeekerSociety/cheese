"""Move one feedback report to a status, recorded as this command and not as a person.

    python -m scripts.feedback_status FB-12 declined \\
      --note "设计如此：…" --by "andy 的运维 agent"

For whoever works the queue from a shell on the box — an operator, or an agent
acting for a feedback admin — instead of signing an access token for that
admin's account and calling `POST /admin/feedback/{id}/status` with it. Such a
token is a bearer credential carrying every power of the account (private
reports, other admin screens) for as long as it lives, wherever it is pasted,
and the timeline it writes says the admin pressed the button. This command can
do one thing, and the timeline says it was done by it.

The step goes through `FeedbackService.set_status`, the same path the admin
button takes, so the timeline entry and the submitter's unread count come with
it. Like the deploy's `scripts.ship_feedback`, nobody pressed anything:
`by_handle` is NULL. What a person would have been recorded as is in the note,
which is shown to the submitter: the reason, then who ran the command.

Prints one line: the move, or why there was none. Setting the status a report
already has writes nothing. Exits 1 when the report does not exist.
"""

import argparse
import asyncio
import sys

from app.core.db import async_session_factory
from app.domain.feedback import claims
from app.domain.feedback.models import FeedbackStatus
from app.domain.feedback.services import FeedbackService


def note_of(reason: str, by: str) -> str:
    return f"{reason.strip()}（{by.strip()} 用运维命令设置）"


async def main(ref: str, status: FeedbackStatus, *, note: str, by: str) -> int:
    async with async_session_factory() as session:
        row = await claims.find(session, ref)
        if row is None:
            print(f"{ref} not_found")
            return 1
        before = row.status
        await FeedbackService(session).set_status(
            row.id, status, by_handle=None, note=note_of(note, by)
        )
        await session.commit()
    shown = f"FB-{row.display_no}"
    if before == status:
        print(f"{shown} already {status.value}")
    else:
        print(f"{shown} {before.value} -> {status.value}")
    return 0


def _nonblank(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must not be blank")
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Move one feedback report to a status, recorded as this command."
    )
    parser.add_argument("ref", help="FB-12 or the report's uuid")
    parser.add_argument("status", choices=[s.value for s in FeedbackStatus])
    parser.add_argument(
        "--note", required=True, type=_nonblank, help="why, shown to the submitter"
    )
    parser.add_argument(
        "--by", required=True, type=_nonblank, help="who is running this command"
    )
    args = parser.parse_args()
    sys.exit(
        asyncio.run(
            main(args.ref, FeedbackStatus(args.status), note=args.note, by=args.by)
        )
    )
