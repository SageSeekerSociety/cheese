"""Every prompt the platform assembles: the prose in `pieces/*.md`, and for each
kind of session a profile (`profiles.py`) that says which pieces it reads, when,
and with which tools. Standard library only: the docs site's generator runs it
on a machine without the backend's dependencies.
"""

from app.domain.prompts.memory import (
    MEMORY_DIR,
    memory_block,
    private_index_pointer,
)
from app.domain.prompts.notices import (
    HIDDEN_TYPES,
    LEGACY_MARKER,
    NoticeType,
    blocked_report_reminder,
    notice_template,
    platform_prompt,
    silence_reminder,
    stale_checklist_reminder,
    stale_progress_reminder,
    still_working_reminder,
    strip_platform_notice,
    without_reminders,
)
from app.domain.prompts.opening import (
    OVERVIEW_DOC_CHAR_BUDGET,
    TOPIC_DOC_CHAR_BUDGET,
    TRACKED_SECTIONS,
    SessionOpening,
    became_task_prompt,
    build_session_opening,
    default_teaching_prompt,
    fit_doc_to_budget,
    opening_changes,
    stages_section,
    task_opening_prompt,
    task_section,
    task_wrap_up,
    teaching_section,
)
from app.domain.prompts.profile import Facts, Kind, Profile, ProfileError
from app.domain.prompts.profiles import PROFILES
from app.domain.prompts.render import (
    check,
    render_notice,
    render_opening,
    render_system,
    selected,
)
from app.domain.prompts.text import PieceError, in_language, piece

#: 读不到主会话系统提示词的每个分身都补这一段，同一份：Claude Code 起的 agent 由
#: SubagentStart hook 补在开头，pi 的分身追加在系统提示词末尾。
SUBAGENT_RULES = piece("system/subagent_rules")


def untitled_task(title_chars: int) -> str:
    """任务还没名字、平台自己又起不了名时，跟着这一轮的消息提醒一句。不进系统
    提示词：起完名就不该再说，而系统提示词在会话里是不变的。"""
    return piece("notices/untitled_task", title_chars=title_chars)


def resume_notice() -> str:
    """The one thing that is true of a turn rather than of its session."""
    return piece("notices/resume")


__all__ = [
    "MEMORY_DIR",
    "OVERVIEW_DOC_CHAR_BUDGET",
    "HIDDEN_TYPES",
    "LEGACY_MARKER",
    "NoticeType",
    "PROFILES",
    "SUBAGENT_RULES",
    "TOPIC_DOC_CHAR_BUDGET",
    "TRACKED_SECTIONS",
    "Facts",
    "Kind",
    "PieceError",
    "Profile",
    "ProfileError",
    "SessionOpening",
    "became_task_prompt",
    "blocked_report_reminder",
    "build_session_opening",
    "check",
    "default_teaching_prompt",
    "fit_doc_to_budget",
    "in_language",
    "memory_block",
    "notice_template",
    "opening_changes",
    "piece",
    "platform_prompt",
    "private_index_pointer",
    "render_notice",
    "render_opening",
    "render_system",
    "resume_notice",
    "selected",
    "silence_reminder",
    "stages_section",
    "stale_checklist_reminder",
    "stale_progress_reminder",
    "still_working_reminder",
    "strip_platform_notice",
    "task_opening_prompt",
    "task_section",
    "task_wrap_up",
    "teaching_section",
    "untitled_task",
    "without_reminders",
]
