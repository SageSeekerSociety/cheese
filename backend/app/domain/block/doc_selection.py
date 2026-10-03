"""Where each top-level block of a document sits in its raw Markdown."""

import re
from dataclasses import dataclass

from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.block.doc_tree import _HEADING_RE, _is_fence, markdown_to_nodes


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
        raise ValidationError(say("docSelectionUnprovable"))
    return blocks
