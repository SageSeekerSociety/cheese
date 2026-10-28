"""Project settings that are said in 综合 when they change, and the line each
change leaves.

Which project settings these are is ``app.domain.project.member_settings``; an
agent's thinking effort, context compaction and skills are the same kind of
choice about one teammate. Who may change them is whoever's role may change the
project's settings (``Permission.edit_settings``, #3830); an agent working in a
conversation does not. What a change may cost stays where it was decided — the
team's plan, the specs the project is offered, whose computer a session may run
on, and the project's tier policy.

Every change is one platform line in the project's 综合, credited to whoever
made it, naming the setting and what it was and is now. The line's ``meta``
(``setting``, ``from``, ``to``, ``by``) is the record of the change, and none of
these settings holds a secret, so the values go there as stored. Nobody is
notified: the line is there to be seen, not answered.
"""

import uuid
from collections.abc import Iterable, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import listing, say
from app.domain.agent.announce import announce
from app.domain.agent.environment import default_environment, stored
from app.domain.agent.model_catalog import model_choices
from app.domain.agent_instance.configuration import AgentConfiguration
from app.domain.identity.handles import agent_instance_handle
from app.domain.machine.environments import environment_words
from app.domain.notice_meta import (
    EVENT_SETTING_CHANGED,
    SEVERITY_INFO,
    WHO_PLATFORM,
    notice,
)
from app.domain.project.member_settings import MEMBER_SETTINGS
from app.domain.project.models import Project
from app.domain.project.services import ProjectService
from app.domain.repository.identity import CREDIT_OWNER, credit_mode
from app.domain.room_task import naming

#: Each member setting → the sentence that names it in the room line.
_NAMES = {
    "default_model": "settingDefaultModel",
    "default_subagent_model": "settingSubagentModel",
    "execution_environment": "settingEnvironment",
    "max_concurrent_turns": "settingMaxConcurrentTurns",
    naming.SETTINGS_KEY: "settingTaskNaming",
    "commit_credit": "settingCommitCredit",
}
if _NAMES.keys() != MEMBER_SETTINGS:
    # i18n-exempt: fails the app at import, for a developer; no screen shows it
    raise RuntimeError("every member setting needs a name to be said by")

#: The fields of an agent's configuration that change what every turn of it
#: costs or can do, and so are said in the room when changed (the rest of it,
#: its name, role and model included, is said nowhere).
_AGENT_NAMES = {
    "effort": "settingEffort",
    "compact_percent": "settingCompact",
    "skills": "settingSkills",
}

_EFFORTS = {
    None: "settingEffortAuto",
    "low": "settingEffortLow",
    "medium": "settingEffortMedium",
    "high": "settingEffortHigh",
    "max": "settingEffortMax",
}


async def change_project_settings(
    db: AsyncSession,
    project: Project,
    patch: Mapping[str, object],
    *,
    remove: Iterable[str] = (),
    by: str,
) -> dict:
    """Write member settings and say each change in the project's 综合, in the
    same transaction. Returns the settings as they now are."""
    changes = await ProjectService(db).change_member_settings(
        project, patch, remove=remove
    )
    for change in changes:
        before = await _words(db, project, change.key, change.before)
        after = await _words(db, project, change.key, change.after)
        if str(before) == str(after):
            # Unset and set to the default read the same: nothing changed for
            # anyone reading the project.
            continue
        await _say(
            db,
            project,
            by=by,
            content=say(
                "projectSettingChanged",
                actor=f"<@{by}>",
                setting=say(_NAMES[change.key]),
                before=before,
                after=after,
            ),
            record={"setting": change.key, "from": change.before, "to": change.after},
        )
    return dict(project.settings or {})


async def say_agent_changes(
    db: AsyncSession,
    project: Project,
    agent_id: uuid.UUID,
    before: AgentConfiguration,
    after: AgentConfiguration,
    *,
    by: str,
) -> None:
    """Say in the project's 综合 each of an agent's costly settings this save
    changed."""
    seat = agent_instance_handle(agent_id)
    changed = (
        (
            "effort",
            before.effort,
            after.effort,
            say(_EFFORTS[before.effort]),
            say(_EFFORTS[after.effort]),
        ),
        (
            "compact_percent",
            before.compact_percent,
            after.compact_percent,
            _compact_words(before.compact_percent),
            _compact_words(after.compact_percent),
        ),
        (
            "skills",
            before.skills,
            after.skills,
            listing(before.skills) if before.skills else say("settingNoSkills"),
            listing(after.skills) if after.skills else say("settingNoSkills"),
        ),
    )
    for field, was, now, said_was, said_now in changed:
        if was == now:
            continue
        await _say(
            db,
            project,
            by=by,
            content=say(
                "agentSettingChanged",
                actor=f"<@{by}>",
                agent=f"<@{seat}>",
                setting=say(_AGENT_NAMES[field]),
                before=said_was,
                after=said_now,
            ),
            record={"setting": field, "agent": seat, "from": was, "to": now},
        )


async def _words(db: AsyncSession, project: Project, key: str, value: object):
    """What the room line calls ``value`` of the member setting ``key``."""
    if key in ("default_model", "default_subagent_model"):
        if value is None:
            return say(
                "settingFollowDefault"
                if key == "default_model"
                else "settingInheritMain"
            )
        labels = {c["id"]: c["label"] for c in model_choices(project.settings)}
        return labels.get(value, value)
    if key == "execution_environment":
        return await environment_words(db, stored(value) or default_environment())
    if key == "max_concurrent_turns":
        # A number, the same in every language; unset is the deployment default.
        return say("settingFollowDefault") if value is None else value
    if key == naming.SETTINGS_KEY:
        mode = naming.naming_mode({naming.SETTINGS_KEY: value})
        return say("settingNamingAuto" if mode == "auto" else "settingNamingManual")
    # `commit_credit`, the one member setting left.
    mode = credit_mode({key: value})
    return say("settingCreditOwner" if mode == CREDIT_OWNER else "settingCreditAgent")


def _compact_words(percent: int | None):
    if percent is None:
        return say("settingFollowDefault")
    return say("settingCompactAt", percent=percent)


async def _say(
    db: AsyncSession, project: Project, *, by: str, content: str, record: dict
) -> None:
    if project.root_topic_id is None:
        return  # Only a project pieced together without its 综合 has none.
    await announce(
        db,
        place_id=project.root_topic_id,
        content=content,
        author=by,
        meta={
            **notice(EVENT_SETTING_CHANGED, severity=SEVERITY_INFO, who=WHO_PLATFORM),
            **record,
            "by": by,
        },
    )
