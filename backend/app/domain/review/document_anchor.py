"""Whether what a comment on a document points at is still in the next version.

A comment on lines finds its lines again by their text (`comment_anchor`). A
comment on a Word document or a deck quoted the words it is about, and is still
in place while those words are; a comment on a cell is in place while its sheet
is, since a cell's address does not move when its value does.
"""

import re

from app.domain.documents.compare import paragraph_texts, sheet_cells, slide_texts
from app.domain.review.comment_place import kind_of

_SPACE = re.compile(r"\s+")


def _squash(text: str) -> str:
    return _SPACE.sub("", text)


def still_there(raw: bytes, path: str, place: str, quote: str) -> bool:
    kind = kind_of(path)
    try:
        if kind == "cell":
            sheet = place.rsplit("!", 1)[0]
            return sheet in sheet_cells(raw)
        wanted = _squash(quote)
        if not wanted:
            return True
        if kind == "page":
            body = "".join(paragraph_texts(raw, path))
        else:
            body = "".join(text for _title, text in slide_texts(raw))
        return wanted in _squash(body)
    except Exception:  # noqa: BLE001 — an unreadable document holds nothing
        return False
