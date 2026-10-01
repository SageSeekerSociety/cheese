"""One coordinate contract: offsets count bytes in the canonical UTF-8 source."""

from app.core.errors import ConflictError, ValidationError
from app.domain.living_doc.services import content_hash


def source_span(source: str, start: int, end: int, exact_hash: str) -> str:
    raw = source.encode("utf-8")
    if not 0 <= start < end <= len(raw):
        raise ValidationError("原文选择范围无效")
    try:
        raw[:start].decode("utf-8")
        selected = raw[start:end].decode("utf-8")
        raw[end:].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("选择范围必须在 UTF-8 字符边界上") from exc
    if content_hash(selected) != exact_hash:
        raise ConflictError("原文选择与提案保存的范围不一致")
    return selected


def replace_span(
    source: str, *, start: int, end: int, exact_hash: str, replacement: str
) -> str:
    source_span(source, start, end, exact_hash)
    raw = source.encode("utf-8")
    return (raw[:start] + replacement.encode("utf-8") + raw[end:]).decode("utf-8")
