"""题目域收到的**输入**：把接口形状的字符串与自由 dict 折成领域里的值。

这一层不碰 FastAPI / Starlette，也不碰任何别的领域的 repository —— 它只是把
``routes/tasks.py`` 里那几张「提交者类型 / 审批类型」的字符串映射，以及
``publish/from-pdf`` 那两份 body 的合并规则搬下来，好让路由函数只留 transport
那一截。
"""

from collections.abc import Sequence

from app.core.errors import BadRequestError

PDF_DRAFT_CONTENT_FIELDS = {"name", "intro", "description"}


def map_submitter_type(value: str) -> int:
    """Map TaskSubmitterTypeDTO string to smallint ordinal."""
    mapping = {
        "USER": 0,
        "TEAM": 1,
    }
    if value not in mapping:
        raise BadRequestError(f"Invalid submitterType: {value}")
    return mapping[value]


def map_approve_type(value: str) -> int:
    """Map ApproveTypeDTO string to smallint ordinal."""
    mapping = {
        "APPROVED": 0,
        "DISAPPROVED": 1,
        "NONE": 2,
    }
    upper = value.upper()
    if upper not in mapping:
        raise BadRequestError(f"Invalid approved value: {value}")
    return mapping[upper]


def map_approve_type_to_int(value: str | None) -> int | None:
    if value is None:
        return None
    return map_approve_type(value)


def apply_pdf_task_options(
    *,
    draft: dict,
    task_options: dict,
) -> dict:
    if not isinstance(draft, dict):
        raise BadRequestError("Each draft must be an object")
    if not isinstance(task_options, dict):
        raise BadRequestError("taskOptions must be an object")

    missing_content_fields: Sequence[str] = [
        field
        for field in PDF_DRAFT_CONTENT_FIELDS
        if field not in draft or str(draft.get(field) or "").strip() == ""
    ]
    if missing_content_fields:
        raise BadRequestError(
            f"Draft missing required content fields: {', '.join(sorted(missing_content_fields))}"  # noqa: E501
        )

    merged = dict(task_options)
    for field in PDF_DRAFT_CONTENT_FIELDS:
        merged[field] = draft[field]

    if "space" not in merged and "space" in draft:
        merged["space"] = draft["space"]
    if "categoryId" not in merged and "categoryId" in draft:
        merged["categoryId"] = draft["categoryId"]

    return merged


def pdf_attachment_ids(task_options: dict) -> list[int]:
    """``taskOptions.attachmentIds``：这一批题共用的材料，勾了才有。

    与其它字段一样是自由 dict（``ConfirmTaskPublishFromPdfRequest`` 不看里面的结
    构），所以形状自己看住：不是列表、或者元素不是整数，是请求写错了 —— 与「没带这
    个键」（不勾任何附件）不是同一件事，不能都当成空。
    """
    raw = task_options.get("attachmentIds")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise BadRequestError("attachmentIds must be a list")
    ids: list[int] = []
    for item in raw:
        try:
            ids.append(int(item))
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"Invalid attachmentIds entry: {item!r}") from exc
    return ids
