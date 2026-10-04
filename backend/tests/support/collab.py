"""A stand-in for the collaboration service, for tests that write the living
document through the backend.

The real service (frontend/collab) holds every room's document live and is the
only thing that stores it. This one holds no document of its own: with no
editor connected, the live document is exactly what the backend stored last.
It answers ``replace`` the way the service does — refuse a writer whose base is
not the live document, otherwise store the result through the backend's own
internal route — so the backend side is exercised for real: its HTTP client,
the internal route's authentication and parsing, the store transaction and the
error it answers with.

``edit`` changes passages of that text the way the service's ``/edit`` does,
as plain string replacement: each ``old`` must occur exactly once. A
suggestion leaves the text alone and is kept here as pending, and every store
reports what is pending, as the service does.

``type_in`` is somebody typing in an editor: a store carrying their handle.
``type_unsaved`` is typing the service holds but has not stored yet; like the
service, this one stores it before refusing a writer that did not see it.
"""

import base64
import uuid

import httpx

from app.domain.living_doc import collab

SECRET = "test-collab-secret"


class FakeCollab:
    def __init__(self, app):
        self._app = app
        #: The service's write check is its own (frontend/collab/writeCheck.ts,
        #: tested there); here a test says what it would answer: (message, line)
        #: for every checked write, or None to take them.
        self.refuse_writes: tuple[str, int] | None = None
        #: Per document: the suggestions pending in it.
        self.pending: dict[str, list[dict]] = {}
        #: Per document: (text, handle) typed since the last store.
        self._unsaved: dict[str, tuple[str, str]] = {}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            # Over HTTP, a failure in the backend is a 500, not an exception.
            transport=httpx.ASGITransport(app=self._app, raise_app_exceptions=False),
            base_url="http://backend",
            headers={"Authorization": f"Bearer {collab._key('internal')}"},
        )

    async def handle(self, request: httpx.Request) -> httpx.Response:
        name = request.url.path.split("/")[3]
        body = _json(request)
        if request.url.path.endswith("/edit"):
            return await self._edit(name, body)
        if body.get("check") and self.refuse_writes is not None:
            message, line = self.refuse_writes
            return httpx.Response(
                422, json={"error": "content", "message": message, "line": line}
            )
        async with self._client() as backend:
            loaded = (await backend.get(f"/internal/collab/documents/{name}")).json()
            unsaved = self._unsaved.pop(name, None)
            live = unsaved[0] if unsaved else loaded["content"]
            stale = live.strip() if body["base"] is None else live != body["base"]
            if stale:
                if unsaved:
                    await self._store(backend, name, *unsaved)
                    loaded = (
                        await backend.get(f"/internal/collab/documents/{name}")
                    ).json()
                return httpx.Response(409, json={"doc_version": loaded["doc_version"]})
            stored = await backend.put(
                f"/internal/collab/documents/{name}",
                json={
                    "state": base64.b64encode(b"yjs").decode(),
                    "content": body["content"],
                    "actors": [body["actor"]],
                    "operation": body["operation"],
                    "suggestions": self.pending.get(name, []),
                },
            )
        if stored.status_code == 409:
            return httpx.Response(
                409,
                json={
                    "error": "operation",
                    "message": stored.json()["error"]["message"],
                },
            )
        if stored.status_code != 200:
            return httpx.Response(502, json={"message": stored.text})
        return httpx.Response(200, json={"stored": stored.json()})

    async def _edit(self, name: str, body: dict) -> httpx.Response:
        async with self._client() as backend:
            text = (await backend.get(f"/internal/collab/documents/{name}")).json()[
                "content"
            ]
            suggest = body["mode"] == "suggest"
            pending = list(self.pending.get(name, []))
            applied = []
            for index, edit in enumerate(body["edits"]):
                found = text.count(edit["old"])
                if found != 1:
                    reason = "not_found" if found == 0 else "ambiguous"
                    return httpx.Response(
                        422, json={"error": "edit", "index": index, "reason": reason}
                    )
                if suggest:
                    sid = f"{body['actor']}:{len(pending) + 1}"
                    pending.append({"id": sid, "author": body["actor"], **_pair(edit)})
                    applied.append({**_pair(edit), "suggestion_id": sid})
                else:
                    text = text.replace(edit["old"], edit["new"])
                    applied.append(_pair(edit))
            self.pending[name] = pending
            stored = await backend.put(
                f"/internal/collab/documents/{name}",
                json={
                    "state": base64.b64encode(b"yjs").decode(),
                    "content": text,
                    "actors": [body["actor"]],
                    "suggestions": pending,
                    "requested_by": body.get("requested_by"),
                    "edits": applied,
                    "suggested": suggest,
                    "reason": body.get("reason"),
                },
            )
        if stored.status_code != 200:
            return httpx.Response(502, json={"message": stored.text})
        return httpx.Response(200, json={"stored": stored.json(), "edits": applied})

    async def type_in(self, room_id: uuid.UUID, content: str, *actors: str) -> dict:
        name = await self._room_document(room_id)
        async with self._client() as backend:
            return await self._store(backend, name, content, *actors)

    async def type_unsaved(self, room_id: uuid.UUID, content: str, actor: str) -> None:
        self._unsaved[await self._room_document(room_id)] = (content, actor)

    async def _room_document(self, room_id: uuid.UUID) -> str:
        """The service name of the room's document. An editor holds a ticket
        before it types, and signing one is what makes the document exist."""
        from app.core.db import get_db
        from app.domain.living_doc.services import Documents
        from app.domain.topic.models import Topic

        # The database the app is answering from, which a test may override.
        sessions = self._app.dependency_overrides.get(get_db, get_db)()
        session = await anext(sessions)
        try:
            room = await session.get(Topic, room_id)
            assert room is not None
            doc = await Documents(session).ensure_for_room(
                room_id=room_id, project_id=room.project_id
            )
            name = collab.document_name(doc.id)
            await session.commit()
        finally:
            await sessions.aclose()
        return name

    async def _store(
        self, backend: httpx.AsyncClient, name: str, content: str, *actors: str
    ) -> dict:
        response = await backend.put(
            f"/internal/collab/documents/{name}",
            json={
                "state": base64.b64encode(b"yjs").decode(),
                "content": content,
                "actors": list(actors),
                "suggestions": self.pending.get(name, []),
            },
        )
        response.raise_for_status()
        return response.json()


def _pair(edit: dict) -> dict:
    return {"old": edit["old"], "new": edit["new"]}


def _json(request: httpx.Request) -> dict:
    import json

    return json.loads(request.content)


def install(monkeypatch, app) -> FakeCollab:
    from app.core.config import settings

    fake = FakeCollab(app)
    monkeypatch.setattr(settings, "collab_secret", SECRET)
    monkeypatch.setattr(collab, "transport", httpx.MockTransport(fake.handle))
    return fake
