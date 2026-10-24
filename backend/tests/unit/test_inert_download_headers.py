"""A download of bytes a user supplied is inert: not sniffed, not allowed to act.

Two routes hand back such bytes — `/uploads/…` and a room file's
`/attachments/raw` — and the frontend keeps its JWT in localStorage, so each
response has to say "do not sniff this into something else" and "do not let this
document do anything". The HTML preview keeps a policy of its own but still
refuses sniffing.
"""

import asyncio
import uuid

from fastapi import Request

from app.api.routes import topics_attachments

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

    async def attachment_file(_db, _project_id, _room_id, _clean, **_):
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

    async def source_bytes(_db, _project_id, _topic_id, _clean, _task, _source, **_):
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


def test_the_html_preview_keeps_its_nosniff_alongside_its_own_policy(monkeypatch):
    """The preview page is allowed its own scripts, but not a re-guess at its type.

    Its CSP is not the inert pair — the page has to run its own inline script —
    so this exit takes `NO_SNIFF` from the shared module and adds that policy.
    Nothing else notices the header going missing: the page still renders, the
    browser just becomes free to reinterpret it.
    """

    async def source_bytes(_db, _project_id, _topic_id, _clean, _task, _source, **_):
        return b"a docx, in principle"

    async def render_to_html(_data, _clean, _endpoint):
        return b"<!DOCTYPE html>\n<html><head><title>x</title></head></html>"

    async def place_or_404(_db, _topic_id):
        return type("Placed", (), {"room": _Room()})()

    monkeypatch.setattr(topics_attachments, "place_or_404", place_or_404)
    monkeypatch.setattr(topics_attachments, "source_bytes", source_bytes)
    monkeypatch.setattr(topics_attachments, "render_to_html", render_to_html)

    response = asyncio.run(
        topics_attachments.attachment_as_html(
            topic_id=uuid.uuid4(),
            path="成果.docx",
            db=object(),
            resolver=_Resolver(),
        )
    )

    assert response.headers["x-content-type-options"] == NOSNIFF
    policy = response.headers["content-security-policy"]
    assert policy != INERT_CSP
    assert "script-src 'unsafe-inline'" in policy
