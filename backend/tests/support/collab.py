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

``type_in`` is somebody typing in an editor: a store carrying their handle.
"""

import base64
import uuid

import httpx

from app.domain.living_doc import collab

SECRET = "test-collab-secret"


class FakeCollab:
    def __init__(self, app):
        self._app = app

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
        async with self._client() as backend:
            loaded = (await backend.get(f"/internal/collab/documents/{name}")).json()
            live = loaded["content"]
            stale = live.strip() if body["base"] is None else live != body["base"]
            if stale:
                return httpx.Response(409, json={"doc_version": loaded["doc_version"]})
            stored = await backend.put(
                f"/internal/collab/documents/{name}",
                json={
                    "state": base64.b64encode(b"yjs").decode(),
                    "content": body["content"],
                    "actors": [body["actor"]],
                    "operation": body["operation"],
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

    async def type_in(self, room_id: uuid.UUID, content: str, *actors: str) -> dict:
        async with self._client() as backend:
            response = await backend.put(
                f"/internal/collab/documents/{collab.document_name(room_id)}",
                json={
                    "state": base64.b64encode(b"yjs").decode(),
                    "content": content,
                    "actors": list(actors),
                },
            )
        response.raise_for_status()
        return response.json()


def _json(request: httpx.Request) -> dict:
    import json

    return json.loads(request.content)


def install(monkeypatch, app) -> FakeCollab:
    from app.core.config import settings

    fake = FakeCollab(app)
    monkeypatch.setattr(settings, "collab_secret", SECRET)
    monkeypatch.setattr(collab, "transport", httpx.MockTransport(fake.handle))
    return fake
