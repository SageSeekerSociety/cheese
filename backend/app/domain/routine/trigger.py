"""A routine's trigger, said in the reader's language on every surface."""

from app.core.sentences import NoticeText, say
from app.domain.routine import schedule
from app.domain.routine.models import Routine, RoutineTrigger


def describe_trigger(routine: Routine) -> NoticeText | str:
    """这一条什么时候跑，一句话。

    盯哪几个频道（执行范围）不在这句话里：界面上它是单独一行，跟着频道名画出来，
    名字由画它的那一层去查（`api/routes/routines.py` 的 `_present`）。
    """
    trigger = RoutineTrigger(routine.trigger)
    if trigger is RoutineTrigger.schedule:
        return schedule.describe(routine.spec, routine.timezone)
    labels = {
        RoutineTrigger.library_file_added: say("routineTriggerLibraryFile"),
        RoutineTrigger.task_closed: say("routineTriggerTaskClosed"),
        RoutineTrigger.card_accepted: say("routineTriggerCardAccepted"),
    }
    return labels[trigger]
