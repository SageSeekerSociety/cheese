"""The 说明 the platform drafts for a person handing a task's changes in.

When a member opens 「提交审阅」, the title and body are written for them from
the change itself: a title saying what the change made (it is the squash
commit's title, so it keeps the repository's convention), and a body only
when the reviewer needs something the title cannot tell them; a title-only
answer leaves the body empty. One question to the
project's task model, no session (`agent.one_shot`), billed to the project.

The person can type over it while it is being written, and the hand-in never
waits on it: a call that fails, is refused or takes too long leaves a line
made from the changed files (`fallback`).
"""

import logging
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableEntityError
from app.domain.agent import one_shot
from app.domain.agent.model_catalog import Scene
from app.domain.prompts import piece

logger = logging.getLogger(__name__)

#: How much of the diff the model reads. A change past it is cut, and the
#: material says where.
DIFF_CHARS = 40_000
#: Enough for a title and a few sentences, with room for a reasoning model's
#: thinking.
_ANSWER_TOKENS = 4000
#: A person is waiting on the panel: past this the line from the files stands.
DRAFT_TIMEOUT_S = 25.0
#: How many file names the fallback line spells out before it counts the rest.
_NAMED = 3
#: The longest title kept from the model; past it the line is cut.
_SUBJECT_CHARS = 120


@dataclass(frozen=True)
class Draft:
    subject: str
    body: str
    #: The model wrote it; False for the line made from the files.
    drafted: bool


def fallback(paths: list[str]) -> str:
    """A title made from the changed files: 「修改 index.html、README.md」."""
    names = [path.rsplit("/", 1)[-1] for path in paths]
    if not names:
        return "修改文件"
    shown = "、".join(names[:_NAMED])
    if len(names) > _NAMED:
        return f"修改 {shown} 等 {len(names)} 个文件"
    return f"修改 {shown}"


def material(
    *, task_title: str, diff: str, recent_subjects: list[str], files: list[str]
) -> str:
    """What the model reads: the task, the repository's recent titles, the
    changed files and the diff, cut at `DIFF_CHARS` with the cut said."""
    shown = diff[:DIFF_CHARS]
    cut = len(diff) > DIFF_CHARS
    parts = [
        f"任务：{task_title}",
        "项目最近的提交标题：\n"
        + ("\n".join(f"- {s}" for s in recent_subjects) or "（没有）"),
        "改动的文件：\n" + "\n".join(f"- {f}" for f in files),
        "改动：\n```diff\n"
        + shown
        + ("\n```\n（改动太长，以上截断，只是前一部分）" if cut else "\n```"),
    ]
    return "\n\n".join(parts)


def _split(content: str) -> tuple[str, str]:
    """The title (first non-empty line) and the body (the rest)."""
    lines = content.strip().strip("`").strip().splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return "", ""
    subject = lines[0].lstrip("#").strip().removeprefix("标题：").strip()
    return subject[:_SUBJECT_CHARS], "\n".join(lines[1:]).strip()


async def draft(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    task_title: str,
    diff: str,
    files: list[str],
    recent_subjects: list[str],
    models: one_shot.ProjectModels,
    timeout: float = DRAFT_TIMEOUT_S,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Draft:
    """The model's 说明 for this change, or the line from the files. The
    call's own deadline is ``timeout``; what it spent is charged either way.
    Every way of ending on the line from the files is logged with why."""
    try:
        bound = await one_shot.bind(session, project_id, Scene.task)
        if bound is None:
            logger.info("hand-in draft: no task model project=%s", project_id)
            return Draft(subject=fallback(files), body="", drafted=False)
        answer = await one_shot.ask(
            project_id=project_id,
            bound=bound,
            system=piece("hand_in/draft"),
            material=material(
                task_title=task_title,
                diff=diff,
                recent_subjects=recent_subjects,
                files=files,
            ),
            models=models,
            timeout=timeout,
            max_tokens=_ANSWER_TOKENS,
            transport=transport,
        )
    except UnprocessableEntityError as refused:
        logger.info("hand-in draft refused project=%s: %s", project_id, refused)
        return Draft(subject=fallback(files), body="", drafted=False)
    except Exception as exc:  # the line from the files stands
        logger.warning(
            "hand-in draft failed project=%s: %s: %s",
            project_id,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return Draft(subject=fallback(files), body="", drafted=False)
    subject, body = _split(answer.content) if answer is not None else ("", "")
    if not subject:
        # A failed call is logged by `one_shot.ask` with its error.
        logger.info("hand-in draft: no title in the answer project=%s", project_id)
        return Draft(subject=fallback(files), body="", drafted=False)
    return Draft(subject=subject, body=body, drafted=True)
