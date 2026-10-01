"""Prove a raw span belongs to one current node without searching its quote."""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ValidationError
from app.domain.block.doc_tree import _HEADING_RE, _is_fence, markdown_to_nodes
from app.domain.block.models import BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.living_doc.services import DocumentJournal
from app.domain.living_doc.source_span import source_span


@dataclass(frozen=True)
class RawBlock:
    start: int
    end: int
    normalized: str


def raw_blocks(source: str) -> list[RawBlock]:
    """Mirror the structural splitter, retaining the original UTF-8 coordinates.

    The parser's output is compared below, so a future parser change cannot
    silently authorize an old offset mapping. Neither repeated text nor quote
    matching participates in locating a node.
    """
    blocks: list[RawBlock] = []
    current: list[tuple[int, int, str]] = []
    in_fence = False
    offset = 0

    def flush():
        if current:
            normalized = "\n".join(line[2] for line in current).strip("\n")
            if normalized.strip():
                blocks.append(RawBlock(current[0][0], current[-1][1], normalized))
            current.clear()

    for match in re.finditer(r"[^\r\n]*(?:\r\n|\r|\n|$)", source):
        whole = match.group()
        if not whole:
            continue
        line = whole.rstrip("\r\n")
        end = offset + len(line.encode("utf-8"))
        item = (offset, end, line)
        offset += len(whole.encode("utf-8"))
        if in_fence:
            current.append(item)
            if _is_fence(line):
                in_fence = False
        elif _is_fence(line):
            flush()
            current.append(item)
            in_fence = True
        elif not line.strip():
            flush()
        elif _HEADING_RE.match(line.lstrip()):
            flush()
            blocks.append(RawBlock(item[0], item[1], line.strip()))
        else:
            current.append(item)
    flush()
    if [block.normalized for block in blocks] != [
        node.content for node in markdown_to_nodes(source)
    ]:
        raise ValidationError("该文档结构不能证明原文坐标，请重新选择")
    return blocks


class DocumentSelections:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.blocks = BlockRepository(session)

    async def snapshot(
        self,
        *,
        room_id: uuid.UUID,
        document_id: uuid.UUID,
        base_version: int,
        selection: dict | None,
    ):
        await DocumentJournal(self.session).lock(room_id)
        doc = await self.blocks.doc_root(room_id)
        if doc is None:
            raise ConflictError("文档已经不存在")
        await self.session.refresh(doc)
        if doc.id != document_id or doc.doc_version != base_version:
            raise ConflictError("文档版本已经变化，请重新选择")
        if selection is None:
            return doc
        nodes = await self.blocks.list_doc_nodes(room_id)
        spans = raw_blocks(doc.content)
        if len(nodes) != len(spans):
            raise ConflictError("文档节点和原文不一致")
        index = next(
            (i for i, node in enumerate(nodes) if str(node.id) == selection["node_id"]),
            None,
        )
        if index is None:
            raise ValidationError("所选节点不属于当前文档")
        node = nodes[index]
        span = spans[index]
        if (
            node.kind != BlockKind.doc_node
            or node.struct_parent != doc.id
            or node.content != span.normalized
        ):
            raise ConflictError("所选节点已经变化")
        start, end = selection["start"], selection["end"]
        if not span.start <= start < end <= span.end:
            raise ValidationError("提案只支持可核验的单块原文范围")
        source_span(doc.content, start, end, selection["exact_hash"])
        return doc
