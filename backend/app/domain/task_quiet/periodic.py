"""Task quietness reminder clock."""

from app.core.background import PeriodicJobs
from app.core.db import SessionFactory
from app.domain.delivery_events import SettleCard
from app.domain.review.ports import Announce, RecordSeatInstruction
from app.domain.room_task.closing import StopsWork
from app.domain.room_task.ports import SweepCheckouts
from app.domain.task_quiet import neglect, sweep


def register_periodic_jobs(
    jobs: PeriodicJobs,
    *,
    sessions: SessionFactory,
    chat: StopsWork,
    settle_card: SettleCard | None,
    announce: Announce,
    record_seat: RecordSeatInstruction,
    machines: SweepCheckouts | None = None,
) -> None:
    jobs.register(
        "quiet task reminder", 3600, lambda: sweep.remind_quiet_tasks(sessions)
    )
    jobs.register(
        "neglected task warning", 3600, lambda: neglect.warn_neglected(sessions)
    )
    jobs.register(
        "neglected task close",
        3600,
        lambda: neglect.close_neglected(
            sessions,
            chat=chat,
            settle_card=settle_card,
            announce_to_origin=announce,
            record_seat=record_seat,
            machines=machines,
        ),
    )
