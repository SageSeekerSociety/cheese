"""What a person's 芝士 is told: its rules, the place it was asked in, and — once —
what was said in the conversation before its session existed."""

from datetime import UTC, datetime

from app.domain.agent.skills import load_skill
from app.domain.assistant.models import AssistantMessage
from app.domain.notification.preferences import local_time
from app.domain.prompts import Facts, Kind, piece, render_system
from app.domain.task.inputs import START_DIRECT
from app.domain.task.models import Task, TaskMembership

#: How much of a conversation's earlier text is carried into a session that
#: never heard it, counted from the latest question back.
EARLIER_CHARS = 24_000


def _when(moment: datetime | None, timezone: str | None) -> str:
    """A moment on the asker's own clock, to the minute — the clock the task
    page shows them, so 芝士 and the page name the same day."""
    if moment is None:
        return "不限"
    return local_time(moment, timezone).strftime("%Y-%m-%d %H:%M")


def _mine(claim: TaskMembership | None, timezone: str | None) -> str | None:
    """The asker's own deadline: their claim's, which is not the task's."""
    if claim is None:
        return None
    if claim.approved != 0:
        return "你的申请：等待出题人通过；通过那天开始做，那时才定下你的提交截止时间"
    return f"你的提交截止：{_when(claim.deadline, timezone)}"


def task_brief(
    task: Task,
    *,
    claim: TaskMembership | None = None,
    timezone: str | None = None,
    materials: list[str] | None = None,
    materials_readable: bool = False,
) -> str:
    """The task as 芝士 is shown it: what anyone who can open the task page
    reads there, and its materials by name (``materials``: what
    ``cheese_read`` reads as ``spaces/<s>/tasks/<t>/files/<name>``, when the
    asker may download them) — plus the asker's own claim
    (``app.domain.task.claims.own_claim``) when they have one. Times are on the
    asker's clock (``timezone``, the one their browser reported). Wrapped so
    the rules can call it material rather than instructions."""
    form = "团队" if task.submitter_type == 1 else "个人"
    clock = local_time(datetime.now(UTC), timezone)
    mine = _mine(claim, timezone)
    lines = [
        f"标题：{task.name}",
        f"简介：{task.intro}",
        f"形式：{form}"
        + (
            f"（{task.min_team_size or 1}–{task.max_team_size} 人）"
            if task.submitter_type == 1 and task.max_team_size
            else ""
        ),
        (
            f"题目截止：{_when(task.deadline, timezone)}"
            "（过了不能再开始；已经开始的，在自己的提交截止之前都能接着做）"
            if task.start_mode == START_DIRECT
            else f"申请截止：{_when(task.deadline, timezone)}"
            "（过了不再收新申请；已经通过的，在自己的提交截止之前都能开始做）"
        ),
        *([mine] if mine else []),
        *(
            ["开始方式：直接开始，点「开始做这道题」就开始，提交截止默认是题目截止"]
            if task.start_mode == START_DIRECT
            else [
                "开始方式：申请制，点「申请做这道题」后等出题人通过，通过后才开始",
                f"限时：{task.default_deadline} 天，从申请通过那天算起",
            ]
        ),
        f"（以上时间都是用户所在时区 {clock.tzinfo}，{clock.tzname()}）",
        f"可多次提交：{'是' if task.resubmittable else '否'}",
        "说明：",
        task.description,
    ]
    if materials:
        lines.append(
            "材料（用 cheese_read 读）："
            if materials_readable
            else "材料（开始做这道题之后才能读取，现在读不到）："
        )
        lines += [
            f"- {name}（spaces/{task.space_id}/tasks/{task.id}/files/{name}）"
            for name in materials
        ]
    return (
        TASK_SCENE
        + "\n"
        + f"<题目 编号 spaces/{task.space_id}/tasks/{task.id}>\n"
        + "\n".join(lines)
        + "\n</题目>"
    )


#: What the task page is for, told with the task (see ``drafts.scene`` for the
#: publish and edit pages).
TASK_SCENE = piece("personal/task_scene")


def system_prompt(*, computer: bool = False) -> str:
    """The rules. They are the same for every conversation and every page, and
    for the session's whole life, so every question shares the cached prefix;
    where the person is goes with the question (``page``). ``computer``: the
    conversation has a sandbox of its own to work in."""
    return render_system(
        Kind.personal, Facts(), skill=load_skill, values={"computer": computer}
    )


_WEEKDAYS = "一二三四五六日"


def about(handle: str, timezone: str | None, memory: str | None = None) -> str:
    """Who the person is, said once, at the start of their conversation: the
    rules are everyone's, so what is theirs goes here — with the index of
    what 芝士 remembers of them (``assistant.memory``), when there is one."""
    clock = local_time(datetime.now(UTC), timezone)
    remembered = (
        f"你记得的事（每条一行，正文用 cheese_memory 读）：\n{memory}\n"
        if memory
        else ""
    )
    return (
        "<关于这个人>\n"
        f"用户名：{handle}（别人 @ 用户、把用户加进项目时用这个）\n"
        f"所在时区：{clock.tzinfo}\n"
        f"{remembered}"
        "</关于这个人>"
    )


def now_line(timezone: str | None) -> str:
    """The person's clock, said with each question."""
    clock = local_time(datetime.now(UTC), timezone)
    return f"（现在是 {clock:%Y-%m-%d %H:%M}，星期{_WEEKDAYS[clock.weekday()]}）"


def page(context: str) -> str:
    """The page the person has open, said with a question when it changed since
    the conversation was last told it."""
    return f"<当前页面>\n{context}\n</当前页面>" if context else ""


def files_said(files: list[tuple[str, str]]) -> str:
    """The files handed over with something the person says, as ``(ref, name)``."""
    if not files:
        return ""
    return "\n\n附上的文件（用 cheese_read 读）：\n" + "\n".join(
        f"- {name}（{ref}）" for ref, name in files
    )


def earlier(messages: list[AssistantMessage]) -> str:
    """What was said before, for a session that has not heard it: the latest
    of it, whole messages only."""
    lines: list[str] = []
    size = 0
    for message in reversed(messages):
        who = {"user": "用户", "platform": "平台"}.get(message.role, "芝士")
        line = f"{who}：{message.text}"
        if size + len(line) > EARLIER_CHARS:
            break
        lines.append(line)
        size += len(line)
    if not lines:
        return ""
    return (
        "下面是这段对话之前说过的话，接着它回答最后的问题。\n<之前的对话>\n"
        + "\n".join(reversed(lines))
        + "\n</之前的对话>"
    )
