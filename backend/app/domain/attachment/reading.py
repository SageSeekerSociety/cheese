"""A file read for 芝士: the text in it, page by page for a PDF, and pictures
of the pages that have no text (a scan) for a model that can look at them.

The person hands 芝士 a file — a lab handout, a syllabus — and asks for a 题目
from it. What comes back has to fit in a question: a PDF is read a range of
pages at a time, and says how many it has, so 芝士 reads on where it needs to.
"""

import base64

import fitz

from app.domain.textfile import decode_text

#: Pages read at once when no range is asked for, and at most.
PAGES_AT_ONCE = 20
#: The most text one read answers with.
TEXT_CHARS = 60_000
#: The most page pictures one read answers with, and how sharp they are.
PICTURES_AT_ONCE = 4
PICTURE_DPI = 110


def _range(pages: str | None, total: int) -> tuple[int, int]:
    """``"3-7"`` / ``"5"`` as 1-based first and last page within the file."""
    first, last = 1, min(total, PAGES_AT_ONCE)
    if pages:
        head, _, tail = pages.replace(" ", "").partition("-")
        try:
            first = max(1, int(head))
            last = int(tail) if tail else first
        except ValueError:
            first, last = 1, min(total, PAGES_AT_ONCE)
    last = min(total, last, first + PAGES_AT_ONCE - 1)
    return first, max(first, last)


def read_document(
    data: bytes, name: str, *, pages: str | None = None, pictures: bool = False
) -> dict:
    """What 芝士 is told of the file: ``text``, and for a PDF the pages read and
    how many there are; ``images`` (base64 PNG) of scanned pages when
    ``pictures``. A file it cannot read says so in ``unreadable``."""
    out: dict = {"name": name, "bytes": len(data), "images": []}
    if name.lower().endswith(".pdf") or data[:4] == b"%PDF":
        try:
            pdf = fitz.open(stream=data, filetype="pdf")
        except Exception:  # not a PDF it can open: say so, not fail the call
            return {**out, "unreadable": "pdf"}
        with pdf:
            total = pdf.page_count
            first, last = _range(pages, total)
            parts: list[str] = []
            scanned: list[int] = []
            for number in range(first, last + 1):
                page = pdf[number - 1]
                text = str(page.get_text()).strip()
                if text:
                    parts.append(f"--- 第 {number} 页 ---\n{text}")
                    continue
                scanned.append(number)
                if pictures and len(out["images"]) < PICTURES_AT_ONCE:
                    png = page.get_pixmap(dpi=PICTURE_DPI).tobytes("png")
                    out["images"].append(
                        {
                            "data": base64.b64encode(png).decode(),
                            "mimeType": "image/png",
                        }
                    )
                    parts.append(f"--- 第 {number} 页 ---\n（扫描页，见附图）")
                else:
                    parts.append(f"--- 第 {number} 页 ---\n（扫描页，没有文字层）")
        text = "\n\n".join(parts)
        return {
            **out,
            "pages": total,
            "first": first,
            "last": last,
            "scanned": scanned,
            "text": text[:TEXT_CHARS],
            "cut": len(text) > TEXT_CHARS,
        }
    text = decode_text(data)
    if text is None:
        return {**out, "unreadable": "binary"}
    return {**out, "text": text[:TEXT_CHARS], "cut": len(text) > TEXT_CHARS}
