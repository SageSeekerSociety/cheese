"""题目级「给 AI 队友的指导」(#944) 的读写胶水。

这一层同时要看 task（读/写 `protocol_override["teaching"]`）与团队文档 /
materials（引用校验）,所以它按 `ensure_teaching_references` 交代的口径落在 api 层
—— api 可以同时 import 两个域，不用给 domain 之间加一条边。抽成单独模块只是因为
`tasks.py` 早已越过体积帽（超帽的文件只许缩不许涨），不是因为这一层该有新规矩。

四级链（空间 → 项目集 → 题目 → 项目）在这里只出现「题目」那一格：读的是这一层
自己写的**原始覆盖**，不是解析后的赢家；写的是**整份替换**，不是深合。
"""

from functools import partial

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.doc_access import ensure_team_documents
from app.api.space_payloads import TeachingRequest, teaching_to_api
from app.core.errors import BadRequestError
from app.domain.materials.services import MaterialService
from app.domain.space.services import ensure_teaching_references
from app.domain.task.models import Task
from app.domain.task.protocol import Teaching


def read_task_teaching(task: Task, *, include_private: bool = False) -> dict:
    """This 题目's own 「给 AI 队友的指导」(#944), in the response shape.

    Read off `protocol_override["teaching"]` — this level's own override, not
    the resolved winner — and reported through `teaching_to_api`, as
    `Space.teaching` and `SpaceCategory.teaching` are. `protocol_override` is
    free-form and may hold other keys, so only the one this feature owns is
    reported; anything else stays internal.

    ``include_private`` carries the 私有指导 (`privatePrompt`) — 出题人可见, so
    only the reads a teacher is looking at ask for it (the 发题页 detail and the
    PATCH reply). Default off: a student's read must not carry it.
    """
    override = getattr(task, "protocol_override", None)
    raw = override.get("teaching") if isinstance(override, dict) else None
    return teaching_to_api(Teaching.from_json(raw), include_private=include_private)


def coerce_teaching(raw: object) -> dict | None:
    """A request body's `teaching` → the stored dict, or `None`.

    `None` is "the field was absent" — not a write at all. The typed bodies
    (JSON create / PATCH) hand in an already-parsed `TeachingRequest`; the PDF
    batch hands in free-form JSON, which goes through the very same strict
    model — so a camelCase dict from the client and a typed body land in the
    column with one spelling.
    """
    if raw is None:
        return None
    if isinstance(raw, TeachingRequest):
        return raw.model_dump()
    if not isinstance(raw, dict):
        raise BadRequestError("teaching must be an object")
    try:
        return TeachingRequest.model_validate(raw).model_dump()
    except ValidationError as exc:
        raise BadRequestError(f"Invalid teaching: {exc}") from exc


async def validate_task_teaching(
    db: AsyncSession, *, teaching: dict | None, actor_user_id: int
) -> None:
    """Refuse a 题目's 指导 that names a document or 课件 nobody can resolve.

    The very check the 项目集 form and the 空间默认 run (`ensure_teaching_references`)
    — a 题目 is one more level of the same config, not a second kind of it, so it
    is held to the same rule. `None` (the field was absent) is not a write at all
    and is skipped; `{}` IS a write — it clears the override — and names nothing,
    which passes.
    """
    if teaching is None:
        return
    await ensure_teaching_references(
        document_check=partial(ensure_team_documents, db),
        material_service=MaterialService.for_lookup(db),
        session=db,
        teaching=teaching,
        actor_user_id=actor_user_id,
    )


def apply_task_teaching(task: Task, teaching: dict | None) -> None:
    """整份替换这道题覆盖里的 `teaching` 那一格，其它覆盖键原样保留。

    Whole-key replacement is the chain's rule (`resolve`'s read), so `{}` here
    means the 题目 says nothing and the 项目集 / 空间 default stands again — not a
    half-inherited merge of the two. `None`是「这一格没被写」—— 直接不写，好让调用
    点把「带没带」和「带了什么」一次说清（见 `coerce_teaching`）。
    """
    if teaching is None:
        return
    task.protocol_override = {
        **(task.protocol_override or {}),
        "teaching": teaching,
    }


def read_task_default_role(task: Task) -> str | None:
    """This 题目's own `default_role` override (#944), or None.

    The RAW override at this level — not the resolved winner — for the same
    reason as `read_task_teaching`: the 发题页 shows what THIS 题目 wrote, and
    blank means 「继承上层的」. A blank string in the column is not a role, so
    it reads back as None.
    """
    override = getattr(task, "protocol_override", None)
    raw = override.get("default_role") if isinstance(override, dict) else None
    return raw if isinstance(raw, str) and raw.strip() else None


def apply_task_default_role(task: Task, role: str | None) -> None:
    """Set or clear this 题目's own `default_role` override; the other override
    keys are kept as they are.

    `None` is "this field was not written" — nothing changes. `""` clears the
    题目's own, so the level above (the 项目集's default) is in force again: the
    page's and 芝士's 沿用上层设置 both mean that. There is no "no
    teammate" value: a 题目 either names a preset or inherits.
    """
    if role is None:
        return
    override = dict(task.protocol_override or {})
    if role.strip():
        override["default_role"] = role.strip()
    else:
        override.pop("default_role", None)
    task.protocol_override = override
