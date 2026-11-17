"""One profile per kind of session (`profile.Profile`).

Read top to bottom, a profile is the whole answer to "what does this kind of
session read": its system prompt's pieces in order and when each is there, the
skills that follow, the opening sections, what follows every message, and its
tools. `docs/site/gen/prompt.py` renders each into `docs/manual/dev/ref-prompt.md`.
"""

from app.domain.prompts.profile import (
    BASE,
    CARRIED,
    SKILLS,
    Facts,
    Include,
    Kind,
    Profile,
)

# -- tools -------------------------------------------------------------------

#: What a session in a channel, a task, a 支线 or a private chat is offered:
#: every platform tool (`sandbox/cheese` `PLATFORM_TOOLS`) and the harness's
#: own file, shell and skill tools.
ROOM_TOOLS = frozenset(
    {
        "chat_send",
        "chat_react",
        "todo_write",
        "cheese_chat",
        "cheese_doc",
        "cheese_app",
        "cheese_canvas",
        "cheese_task",
        "cheese_card",
        "cheese_notify",
        "cheese_web",
        "cheese_help",
        "cheese_room",
        "cheese_session",
        "cheese_library",
        "cheese_ask",
        "cheese_feedback",
        "cheese_run_records",
        "cheese_routine",
        "cheese_skill",
        "platform_request",
        "Read",
        "Write",
        "Edit",
        "Bash",
        "Glob",
        "Grep",
        "Skill",
        "Agent",
    }
)

#: The channel's main line: it talks with the members and coordinates the
#: channel's tasks, so it holds what that takes and none of the tools that do
#: the work — that is a task's. It reads files and looks things up (`Read`,
#: `Bash`) to write a clear task, and writes only its memory (`Write`, `Edit`,
#: held to `~/.cheese/memory/` by the platform). `cheese_doc` reads and edits
#: the project's goals, which its opening shows.
MAIN_LINE_TOOLS = frozenset(
    {
        "chat_send",
        "chat_react",
        "cheese_chat",
        "cheese_task.open",
        "cheese_task.tell",
        "cheese_task.look",
        "cheese_task.close",
        "cheese_task.reopen",
        "cheese_doc.get",
        "cheese_doc.edit",
        "cheese_library",
        "cheese_web",
        "cheese_help",
        "cheese_session.status",
        "platform_request",
        "Skill",
        "Read",
        "Bash",
        "Write",
        "Edit",
    }
)

#: A document's 芝士 (`document/question.py`): the document, the project's
#: search, memory and library, read-only file tools on the task's machine.
DOCUMENT_TOOLS = frozenset(
    {
        "cheese_doc.get",
        "cheese_doc.edit",
        "cheese_project_search",
        "cheese_memory.project",
        "cheese_library.read",
        "read",
        "ls",
        "find",
        "grep",
    }
)

#: A person's own 芝士 (`sandbox/cheese` `DELEGATED_TOOLS`, `personal/`).
PERSONAL_TOOLS = frozenset(
    {
        "cheese_inbox",
        "cheese_find",
        "cheese_help",
        "cheese_read",
        "chat_send",
        "cheese_task.open",
        "cheese_card.decide",
        "cheese_room",
        "cheese_people",
        "cheese_space",
        "cheese_settings",
        "cheese_automation",
        "cheese_notify",
        "cheese_todo",
        "cheese_memory",
        "cheese_web",
        "cheese_project_search",
        "cheese_library",
        "cheese_problem",
        "cheese_subagent_start",
        "bash",
        "read",
        "write",
        "edit",
        "ls",
        "find",
        "grep",
    }
)

# -- system prompts ----------------------------------------------------------


def _has_doc(facts: Facts) -> bool:
    """这里有实况文档的位置（空着也算）"""
    return facts.has_doc


def _memory_synced(facts: Facts) -> bool:
    """这一轮的骨架会把记忆文件同步回平台（Claude Code）"""
    # The memory rules follow the harness this turn runs: one that does not
    # sync the tree back would read them and think it writes project memory.
    return facts.memory_synced


def _working_directory(facts: Facts) -> bool:
    """会话的命令带着工作目录的短名 `$T`（Claude Code）"""
    # `$T` is in the environment only of the harness that puts it there; any
    # other would `cd` into a directory that does not exist.
    return facts.working_directory


# 规矩进系统提示词，现状进开场：系统提示词在一个会话里一字不变，前缀缓存才接得上。
def _room_system(memory: str) -> tuple[Include, ...]:
    """The rules of a session in a room, with ``memory`` the memory rules it
    reads: a private chat's name both scopes, a shared place's only the team's."""
    return (
        Include("system/own_name", needs=("name",)),
        Include(BASE),
        Include("system/platform_rules"),
        Include("system/always_push"),
        Include("system/working_directory", when=_working_directory),
        Include("system/todo_write"),
        Include("system/task_state"),
        Include("system/ask_only_cheese_ask"),
        Include("writing", slots={"speaking": "在对话里说话"}),
        Include("system/doc_skill"),
        Include(SKILLS),
        Include(
            "system/doc_section", when=_has_doc, skill_slots={"doc_form": "doc-form"}
        ),
        Include(memory, when=_memory_synced),
        Include("system/project_skills"),
        Include("system/role", needs=("role",)),
    )


#: What a room session's opening may hold (`opening.build_session_opening`).
ROOM_OPENING = frozenset(
    {
        "thread",
        "channel",
        "tasks",
        "task",
        "teaching",
        "stages",
        "package",
        "topics",
        "artifacts",
        "roster",
        "overview",
        "doc",
        "memory",
        "environment",
    }
)

_ROOM_REMINDERS = (
    "notices/silence",
    "notices/still_working",
    "notices/checklist",
    "notices/progress_none",
    "notices/progress",
    "notices/blocked",
)


#: The main line's own rulebook, in place of the rules for doing the work:
#: those (push, checklist, task state, asking with cards, documents, project
#: skills) belong to the task it opens.
MAIN_LINE_SYSTEM = (
    Include("system/own_name", needs=("name",)),
    Include(BASE),
    Include("system/platform_rules_main_line"),
    Include("system/working_directory", when=_working_directory),
    Include("main_line/rules"),
    Include("main_line/teammates"),
    Include("main_line/speaking"),
    Include("writing", slots={"speaking": "说话"}),
    Include("memory/team", when=_memory_synced),
    Include(SKILLS),
    Include("system/role", needs=("role",)),
)

#: What the main line is told when it opens: where it is, the channel's tasks,
#: who is here and what the project is for. Not a task's living document nor
#: the deliverables list: they are a task's to keep.
MAIN_LINE_OPENING = frozenset(
    {
        "channel",
        "tasks",
        "teaching",
        "stages",
        "package",
        "topics",
        "roster",
        "overview",
        "memory",
        "environment",
    }
)


def _room(kind: Kind, skills: tuple[str, ...], memory: str) -> Profile:
    return Profile(
        kind=kind,
        system=_room_system(memory),
        tools=ROOM_TOOLS,
        skills=skills,
        opening=ROOM_OPENING,
        notice="notices/publication",
        reminders=_ROOM_REMINDERS,
    )


PROFILES: dict[Kind, Profile] = {
    Kind.main_line: Profile(
        kind=Kind.main_line,
        system=MAIN_LINE_SYSTEM,
        tools=MAIN_LINE_TOOLS,
        skills=(CARRIED,),
        opening=MAIN_LINE_OPENING,
        notice="notices/main_line",
        reminders=("notices/silence",),
        reply_tools=("chat_send", "chat_react"),
        reply_held=False,
        answers_the_platform=True,
        narrowed=True,
        writes="memory",
        effort="high",
    ),
    Kind.task: _room(Kind.task, ("chat", CARRIED), "memory/team"),
    Kind.thread: _room(Kind.thread, ("chat", CARRIED), "memory/team"),
    # 私聊是名册两席的房间（结论 19），所以它先拿房间那份发布契约，private-chat
    # 只补私聊独有的那几条。
    Kind.private: _room(
        Kind.private, ("chat", CARRIED, "private-chat"), "memory/instructions"
    ),
    Kind.document: Profile(
        kind=Kind.document,
        system=(
            Include("document/rules"),
            # Every session here writes into a document, so the writing rules
            # and the whole document guide are in the prompt rather than a
            # skill the agent may not load. The guide points at its blocks
            # reference by the path it has as a skill; here that file follows
            # under the same name.
            Include("writing", slots={"speaking": "在对话里说话"}),
            Include("document/guide", skill_slots={"body": "cheese-docs"}),
            Include("document/ref_blocks", skill_slots={"body": "doc-blocks"}),
            Include("document/ref_live", skill_slots={"body": "doc-live"}),
            Include("document/miniapp", skill_slots={"body": "miniapp"}),
            Include("document/ref_apps", skill_slots={"body": "doc-apps"}),
            Include("document/goals", needs=("goals",)),
            Include("document/memory", needs=("memory",)),
        ),
        tools=DOCUMENT_TOOLS,
    ),
    Kind.personal: Profile(
        kind=Kind.personal,
        system=(
            Include("personal/rules"),
            Include("personal/computer", needs=("computer",)),
        ),
        tools=PERSONAL_TOOLS,
    ),
    Kind.dream_team: Profile(
        kind=Kind.dream_team, system=(Include("dream/team"),), tools=frozenset()
    ),
    Kind.dream_private: Profile(
        kind=Kind.dream_private, system=(Include("dream/private"),), tools=frozenset()
    ),
    Kind.situation: Profile(
        kind=Kind.situation,
        system=(
            Include("situation/rules"),
            Include("writing", slots={"speaking": "在对话里说话"}),
            Include(
                "situation/guide",
                skill_slots={"guide": "cheese-docs", "blocks": "doc-blocks"},
            ),
        ),
        tools=frozenset(),
    ),
}
