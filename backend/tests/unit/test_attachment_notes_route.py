"""`/topics/{id}/attachments/notes` — what the panel asks for a deck's notes.

The route reads the same bytes as the PDF route for the same reader, so the two
things worth pinning at this level are the ones the domain test cannot see: only
a deck is answered at all (a spreadsheet or a Word file has no notes, and
answering `[]` would read as "this deck has none"), and the reply carries the
version of the bytes it read — the panel drops a reply that arrives after the
file changed.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.auth import get_actor_resolver
from app.api.request_deps import get_db
from app.api.routes.topics_attachments import router
from app.core.errors import register_exception_handlers

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def deck_bytes(note: str = "先讲结论。") -> bytes:
    """The smallest real deck: one slide whose notes hold one line."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(
            "ppt/presentation.xml",
            f'<p:presentation xmlns:p="{P}" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>',
        )
        z.writestr(
            "ppt/_rels/presentation.xml.rels",
            f'<Relationships xmlns="{PKG}">'
            '<Relationship Id="rId1" Target="slides/slide1.xml"/></Relationships>',
        )
        z.writestr(
            "ppt/slides/slide1.xml",
            f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree/></p:cSld></p:sld>',
        )
        z.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            f'<Relationships xmlns="{PKG}">'
            '<Relationship Id="n1" Target="../notesSlides/notesSlide1.xml"/></Relationships>',
        )
        z.writestr(
            "ppt/notesSlides/notesSlide1.xml",
            f'<p:notes xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree><p:sp>'
            "<p:nvSpPr><p:nvPr><p:ph/></p:nvPr></p:nvSpPr><p:txBody>"
            f"<a:p><a:r><a:t>{note}</a:t></a:r></a:p></p:txBody></p:sp>"
            "</p:spTree></p:cSld></p:notes>",
        )
    return buffer.getvalue()


class _Place:
    def __init__(self, topic_id: uuid.UUID):
        self.room = SimpleNamespace(id=topic_id, project_id=uuid.uuid4())


class _Resolver:
    async def resolve(self, **_kwargs):
        return object()

    async def authorize_topic(self, *_args, **_kwargs):
        return None


@pytest.fixture
def app(monkeypatch):
    """The router alone, with the database and the actor swapped out.

    A router-level fixture rather than the whole application: what is under test
    is this route's own decisions, and the full app would bring a database along
    for none of them. The byte source and the path check are replaced on the
    module, since they are plain calls rather than FastAPI dependencies.
    """
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(router)
    application.dependency_overrides[get_db] = lambda: None
    application.dependency_overrides[get_actor_resolver] = _Resolver
    return application


@pytest.fixture
def served(monkeypatch, app):
    """Point the route at given bytes on disk, keeping the real parsing."""

    def _serve(data: bytes):
        async def place(_session, topic_id):
            return _Place(topic_id)

        async def source(*_args, **_kwargs):
            return data

        monkeypatch.setattr("app.api.routes.topics_attachments.place_or_404", place)
        monkeypatch.setattr("app.api.routes.topics_attachments.source_bytes", source)

        async def may_read(*_args, **_kwargs):
            return False

        monkeypatch.setattr("app.api.routes.topics_attachments.may_read_path", may_read)

    return _serve


async def call(
    app: FastAPI, path: str, topic_id: uuid.UUID | None = None
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://viewer"
    ) as browser:
        return await browser.get(
            f"/topics/{topic_id or uuid.uuid4()}/attachments/notes?path={path}"
        )


async def test_a_deck_answers_with_its_notes_and_the_version_it_read(app, served):
    served(deck_bytes("先讲结论。"))
    response = await call(app, "汇报.pptx")
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["notes"] == ["先讲结论。"]
    assert body["readable"] is True
    # The version is the digest of the bytes read, not the path or the mtime: a
    # reply that arrives after the file changed must be droppable by comparing it.
    assert len(body["version"]) == 16


async def test_a_file_that_is_not_a_deck_is_refused_rather_than_answered_empty(
    app, served
):
    # `[]` would say "this deck has no notes"; a spreadsheet or a Word file is a
    # different answer and has to look different.
    served(deck_bytes())
    response = await call(app, "成绩.xlsx")
    assert response.status_code != 200


async def test_bytes_that_are_not_a_deck_at_all_come_back_unreadable(app, served):
    # The suffix says .pptx but the bytes are not a package: "readable" is what
    # tells the panel it has nothing to show, rather than an empty deck.
    served(b"this is not a zip")
    response = await call(app, "汇报.pptx")
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["readable"] is False
    assert body["notes"] == []
