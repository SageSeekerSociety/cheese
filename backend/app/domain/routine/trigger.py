"""A routine's trigger, said in the reader's language on every surface."""

from app.core.sentences import NoticeText, say
from app.domain.routine import schedule
from app.domain.routine.models import Routine, RoutineTrigger


def describe_trigger(routine: Routine) -> NoticeText | str:
    trigger = RoutineTrigger(routine.trigger)
    if trigger is RoutineTrigger.schedule:
        return schedule.describe(routine.spec, routine.timezone)
    labels = {
        RoutineTrigger.library_file_added: say("routineTriggerLibraryFile"),
        RoutineTrigger.task_closed: say("routineTriggerTaskClosed"),
        RoutineTrigger.card_accepted: say("routineTriggerCardAccepted"),
    }
    scope = (
        say("routineTriggerRoom")
        if routine.spec.get("scope") == "room"
        else say("routineTriggerProject")
    )
    return say("routineTriggerEvent", label=labels[trigger], scope=scope)
