"""The pair of headers that makes a download inert is written in one place.

Two routes hand back bytes a user supplied — `/uploads/…` and a room file's
`/attachments/raw` — and the frontend keeps its JWT in localStorage, so each
response has to say "do not sniff this into something else" and "do not let this
document do anything". The defence is only as good as its weakest copy: a
response that carries its own copy of the pair stops moving when the shared one
learns something new, and nothing points at the copy it left behind.

The first two tests drive each exit and read the response it actually builds;
the third reads the modules and fails if the values are spelled out there again.
That third rule is what catches the copy coming back — the responses stay
correct while a copy exists, which is exactly why nobody notices it.
"""

import ast
import asyncio
import uuid
from pathlib import Path

from fastapi import Request

from app.api.routes import topics_attachments, uploads

NOSNIFF = "nosniff"
INERT_CSP = "default-src 'none'; sandbox"


class _Room:
    id = uuid.uuid4()
    project_id = uuid.uuid4()


class _Resolver:
    """The gate the exits run, saying yes to everything.

    What it decides is not what this file is about: another test owns whether an
    unverified caller or a non-member is turned away.
    """

    async def require_verified_caller(self, **_kwargs):
        return None

    async def resolve(self, **_kwargs):
        return object()

    async def authorize_topic(self, _actor, **_kwargs):
        return None


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def test_the_raw_exit_sends_the_inert_pair(monkeypatch, tmp_path):
    """A pasted .html leaves as a download, inert in the origin that served it."""
    stored = tmp_path / "report.html"
    stored.write_bytes(b"<script>alert(document.cookie)</script>")

    async def attachment_file(_db, _project_id, _room_id, _clean):
        return stored

    async def place_or_404(_db, _topic_id):
        return type("Placed", (), {"room": _Room()})()

    monkeypatch.setattr(topics_attachments, "place_or_404", place_or_404)
    monkeypatch.setattr(
        topics_attachments.library_records, "attachment_file", attachment_file
    )

    response = asyncio.run(
        topics_attachments.attachment_raw(
            topic_id=uuid.uuid4(),
            path="uploads/abc123/report.html",
            db=object(),
            resolver=_Resolver(),
            request=_request(),
            download=True,
        )
    )

    assert response.headers["x-content-type-options"] == NOSNIFF
    assert response.headers["content-security-policy"] == INERT_CSP


def test_the_pdf_exit_sends_the_inert_pair(monkeypatch):
    """A converted deliverable is still bytes from this origin — same pair."""

    async def source_bytes(_db, _project_id, _topic_id, _clean, _task, _source):
        return b"a docx, in principle"

    async def render_to_pdf(_data, _clean, _endpoint):
        return b"%PDF-1.4\n%%EOF"

    async def place_or_404(_db, _topic_id):
        return type("Placed", (), {"room": _Room()})()

    monkeypatch.setattr(topics_attachments, "place_or_404", place_or_404)
    monkeypatch.setattr(topics_attachments, "source_bytes", source_bytes)
    monkeypatch.setattr(topics_attachments, "render_to_pdf", render_to_pdf)

    response = asyncio.run(
        topics_attachments.attachment_as_pdf(
            topic_id=uuid.uuid4(),
            path="成果.docx",
            db=object(),
            resolver=_Resolver(),
        )
    )

    assert response.headers["x-content-type-options"] == NOSNIFF
    assert response.headers["content-security-policy"] == INERT_CSP


def _written_strings(module) -> set[str]:
    """Every string literal in `module`, except the docstrings that explain it.

    Docstrings are prose about the rule; a literal is the rule being written out
    again. The distinction matters here because `uploads.py`'s module docstring
    says `nosniff` while the code no longer does.
    """
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


def test_the_exits_do_not_write_the_pair_themselves():
    """One sentence, one place: neither response module spells the values out."""
    for module in (topics_attachments, uploads):
        written = _written_strings(module)
        assert NOSNIFF not in written, f"{module.__name__} writes nosniff itself"
        assert INERT_CSP not in written, f"{module.__name__} writes the CSP itself"
