"""The project settings any member of the project may change.

Everything else in a project's settings is its managers' (the project owner and
the owners and admins of its team). These are the everyday choices of how the
project works — which model, which environment a new agent gets, how tasks are
named, whose name a commit carries — and the people and agents doing that work
may change them. Spend stays bounded by what the managers keep: the team's plan
and the project's tier policy still decide what a model or a machine may cost.

What makes a setting safe to open is that nobody learns of a change by
accident: every change is said in the project's 综合, credited to whoever made
it. ``ProjectService.merge_settings`` refuses these keys, so the only write is
``ProjectService.change_member_settings``, whose one caller
(``app/api/member_settings.py``) says each change in the room.
"""

from dataclasses import dataclass
from typing import Final

#: The ``Project.settings`` keys a member may change. ``task_naming`` is
#: ``room_task.naming.SETTINGS_KEY``, written out here because this layer may
#: not read that one; ``app/api/member_settings.py`` checks the two agree.
MEMBER_SETTINGS: Final = frozenset(
    {
        "default_model",
        "default_subagent_model",
        "execution_environment",
        "task_naming",
        "forge_requester_coauthor",
    }
)


@dataclass(frozen=True)
class SettingChange:
    """One member setting as it was before a write and as it is after. ``None``
    is a setting nobody chose, which the project reads as its default."""

    key: str
    before: object
    after: object
