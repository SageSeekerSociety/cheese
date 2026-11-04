"""This room's current preview: what 芝士 last pointed at, and its bytes.

Third slice of `app/api/routes/topics.py` (arch review C-backend.md section 3.3),
and the smallest one: two routes, both about the one thing the panel puts on
screen. `GET /topics/{topic_id}/preview` says which artifact that is -- null when
none is set -- how it should be rendered, and, for an app, whether the tunnel and
the app behind it are actually up; `GET .../preview/file` reads the bytes, from
the room's current artifact or from the `path` a `<&path>` chip in a message names.

Where the shared names went. `_actor_in_place` and `DbSession` are still defined in
topics.py and read by its own handlers too, so they stay and this module imports
them; the two room-file rules this module read from there, `ARTIFACT_MIME` and
`clean_artifact_path`, now live in `app.domain.project.room_files`.
`BlockRepository` is the one name read here that topics.py merely imports -- and it
is imported from
topics.py rather than from `app.domain.block.repositories` on purpose. The guard
in `tests/unit/test_domain_import_guard.py` ratchets (route module, repository
module) pairs, and a direct import would add a line to that ratchet: a move must
change where a handler lives and nothing else, so this slice adds no exemption to
any boundary. When a later slice takes topics.py's last `BlockRepository` reader
with it, that line can move to the domain and the exemption it needs today falls
away with it. `preview_origin` is still imported inside the handler that uses it,
exactly as it was: this slice is a move, so it does not re-decide where an import
lives. `topics.py` imports nothing from this module, so there is no cycle.

Ordering. This module sorts after `topics.py` (`.` < `_`), so its router mounts
after that file's. Nothing registered earlier can shadow either path: no route
anywhere has a parameter where `preview` sits, so `/topics/{topic_id}/preview` and
`.../preview/file` have no parameterized route to lose to. (`preview_sessions.py`
is the other module under this prefix; its `/{topic_id}/preview-session` is a
literal segment, so the two never meet.) A request per path confirms each still
reaches its own handler.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the same
`APIRouter(prefix="/topics", tags=["topics"])` is all it takes.
"""

import asyncio
import uuid

from fastapi import APIRouter

from app.api.auth import ActorResolverDep
from app.api.preview_app_state import app_state
from app.api.preview_host import preview_origin
from app.api.project_library import may_read_path
from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.topics_deps import BlockRepository, _actor_in_place
from app.core.errors import NotFoundError
from app.domain.library import records as library_records
from app.domain.library import service as library
from app.domain.project.room_files import ARTIFACT_MIME, clean_artifact_path
from app.domain.remote_mcp import apps
from app.domain.room_task.place import place_or_404

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("/{topic_id}/preview")
async def get_preview(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """This room's current preview (spec §7.1): the artifact 芝士 last pointed at,
    as {path, mime}. Null when none is set — the client may fall back to scanning
    the worktree. Content is fetched separately via the guarded file reader."""
    place = await place_or_404(db, topic_id)
    await _actor_in_place(resolver, place)
    # The conversation's own: a task's preview is what its session last showed.
    topic_id = place.conversation_id
    art = await BlockRepository(db).latest_artifact(place.conversation_id)
    if art is None:
        return ok(None)

    if art.mime_type == ARTIFACT_MIME["mcp-app"]:
        # A third-party server's HTML View (`ui://`). No bytes are read here and
        # no tunnel is probed: the content host fetches the resource through that
        # server's connection when the frame asks for it, under its own stricter
        # policy. `path` names the resource, `server` names whose it is.
        parts = apps.pointer_parts(art.content)
        if parts is None:
            return ok(None)
        server, uri = parts
        return ok(
            {
                "kind": "mcp-app",
                "url": preview_origin(topic_id) + "/",
                "path": uri,
                "server": server,
                "mime": art.mime_type,
                "artifact_id": str(art.id),
            }
        )
    if art.mime_type == ARTIFACT_MIME["app"]:
        # Knocked live through the tunnel every time the panel asks; see
        # `app_state` for why the tunnel and the app are reported apart.
        state = await app_state(topic_id, art.author)
        return ok(
            {
                "kind": "app",
                "path": art.content,
                "mime": art.mime_type,
                # Every executable preview stays outside the platform origin.
                "url": (preview_origin(topic_id) + "/" if state.alive else None),
                "tunnel_up": state.tunnel_up,
                "instance": state.instance,
                "artifact_id": str(art.id),
            }
        )
    return ok(
        {
            "kind": "file",
            "url": preview_origin(topic_id) + "/",
            "version": await asyncio.to_thread(
                library.preview_file_version,
                place.project_id,
                place.room_id,
                art.content,
            ),
            "path": art.content,
            "mime": art.mime_type,
            # Which artifact this is, so a client can tell "芝士 pointed at
            # something new" from "the same preview, re-fetched" — re-pointing at
            # the same path is a new preview too, so the path cannot carry this.
            "artifact_id": str(art.id),
        }
    )


@router.get("/{topic_id}/preview/file")
async def preview_file(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    path: str | None = None,
) -> dict:
    """The file the preview is showing: the room's current artifact, or `path`.

    A `<&path>` chip in a message names a file without saying which store holds
    it, and a room's own files are here rather than on a branch. Reading one by
    path is how a reader gets from that chip to the file, instead of to a
    listing that does not contain it — including a `library/…` chip, which is
    what 芝士 writes once it has read something the project was given.
    """
    place = await place_or_404(db, topic_id)
    await _actor_in_place(resolver, place)
    if path:
        return ok(
            await library_records.read_attachment_text(
                db,
                place.project_id,
                place.room_id,
                clean_artifact_path(path),
                with_task=await may_read_path(
                    db, place.project_id, clean_artifact_path(path)
                ),
            )
        )
    art = await BlockRepository(db).latest_artifact(place.conversation_id)
    if art is None or art.mime_type in (
        ARTIFACT_MIME["app"],
        ARTIFACT_MIME["mcp-app"],
    ):
        raise NotFoundError("No file preview")
    return ok(library.read_room_text_file(place.project_id, place.room_id, art.content))
