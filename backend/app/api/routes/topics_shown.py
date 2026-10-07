"""The room's artifacts on show: publish one, list them, keep one.

Seventh slice of `app/api/routes/topics.py` (arch review C-backend.md section
3.3), after `topics_attachments.py` (#2171), `topics_documents.py` +
`topics_preview.py` (#2175), `topics_side_routes.py` (#2190),
`topics_compute.py` (#2197) and `topics_title.py` (#2201). topics.py is 2,915
lines against a 1,500-line cap that only ratchets down.

What moves: `POST /topics/{topic_id}/shown` (芝士 摆一份东西出来给这个房间看的那个
接口，`cheese show` 用它), `GET /topics/{topic_id}/shown` (everything the room has
shown, newest first) and `POST /topics/{topic_id}/shown/save` (a person keeping one
of them in the project's 资料库), plus the two names only these three read --
`_reject_unreachable_app` and `_PREVIEW_ATTACH_WAIT_S`, the grace window it gives a
machine's tunnel.

Where the shared names went. The four room-file rules -- `ARTIFACT_MIME`,
`MAX_ARTIFACT_BYTES`, `artifact_kind_for` and `clean_artifact_path` -- now live in
`app.domain.project.room_files`, their own group there, and `record_shown` became
`add_shown_block` in `app.domain.block.shown` (it writes the block; the broadcast
stays with each caller, so a caller keeps its own order and its own commit). The
source read (`_source_bytes`) and the binding check (`_bind_source_task`) went to
their own homes -- `app.api.routes.topics_file_sources.source_bytes` (API read
orchestration) and `TaskService.require_source_in_room` in `app.domain.room_task`,
which is a rule about a task, not about HTTP. None of them is read from topics.py
any more.
`BlockRepository` is still imported from topics.py rather than from
`app.domain.block.repositories`, the shape `topics_side_routes.py` uses: the guard
in `tests/unit/test_domain_import_guard.py` ratchets (route module, repository
module) pairs, and topics.py still reads `BlockRepository` in a dozen handlers, so
reading it through topics.py adds no exemption to any boundary. Nothing here
imports an `app.domain.*.models` module, so `.importlinter` and its C2 baseline do
not move either.

Ordering. This module sorts after `topics.py` and after every other `topics_*`
module (`_` > `.`, and `shown` > `preview`), so its three paths mount later in the
route table than they did inside topics.py. No route registered before them has a
parameter where `shown` sits, so none of the three loses its first full match;
resolving every path in the table confirms each still reaches the handler it did
before, now under `app.api.routes.topics_shown`.

One integration test monkeypatches `_PREVIEW_ATTACH_WAIT_S` and now points at this
module (the shape #2171 and #2197 used): that is the module the handler reading it
moved to, and without the repoint the patch no longer takes effect.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the declaration below, with the
same prefix and tags, is all it takes.
"""

import base64
import binascii
import uuid

from fastapi import APIRouter

from app.api.auth import ActorResolverDep
from app.api.deps import get_broker
from app.api.response import ok, page
from app.api.routes.topics import BlockRepository, DbSession, _actor_in_place
from app.core.config import settings
from app.core.errors import ValidationError
from app.core.sentences import listing, say
from app.domain.agent.preview_hub import preview_hub
from app.domain.agent.preview_owner import inspect_owner
from app.domain.block.shown import add_shown_block
from app.domain.project import room_files
from app.domain.project.room_files import (
    ARTIFACT_MIME,
    MAX_ARTIFACT_BYTES,
    artifact_kind_for,
    clean_artifact_path,
)
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topics"])


# How long ``cheese serve`` may wait for the helper it just started to finish its
# upgrade. It declares the preview in the same breath as starting the tunnel, so
# without this the platform would refuse a preview that is one round trip away.
_PREVIEW_ATTACH_WAIT_S = 8.0


async def _reject_unreachable_app(topic_id: uuid.UUID, seat: str) -> None:
    """Refuse an app artifact the platform provably cannot render (``cheese serve``).

    Setting it used to always succeed, so 芝士 announced 「预览已就绪」 while the
    panel showed 「应用暂时不在线」. Two separate things can be missing and they
    read differently to whoever has to fix them: the tunnel (nothing on that
    machine is carrying a preview out) and the app behind it (the tunnel is up and
    the declared port answers nothing).
    """
    inspection = None
    if settings.preview_connection_mode == "owner":
        inspection = await inspect_owner(
            topic_id,
            seat,
            wait_ms=min(8000, int(_PREVIEW_ATTACH_WAIT_S * 1000)),
            probe=True,
        )
    tunnel_up = (
        inspection.tunnel_up
        if inspection is not None
        else await preview_hub.wait_online(topic_id, seat, _PREVIEW_ATTACH_WAIT_S)
    )
    if not tunnel_up:
        raise ValidationError(say("previewTunnelDown"))
    alive = (
        inspection.alive
        if inspection is not None
        else await preview_hub.probe(topic_id, seat)
    )
    if not alive:
        raise ValidationError(say("previewPortSilent"))


@router.post("/{topic_id}/shown")
async def show_in_room(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """芝士 摆一份东西出来给这个房间里的人看 —— `cheese show` (#1085 结论四)。

    摆出来的东西留在房间里：它是这一轮做的，谁要拿走就拿走，不因此成为项目的产物
    （那要人按一下「保存到项目」）。最后摆的那一样同时是这个房间的当前预览。
    任务的会话摆的是那个任务的：进任务的列表、成为任务的预览；文件仍存在房间里。"""
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    # The teammate that showed it, when a teammate did. A room may seat several,
    # and for an app the author is also WHICH app: each teammate serves from its
    # own checkout through its own tunnel, and the preview follows the author.
    # Taken from the credential, because the helper's tunnel is keyed by the
    # same claim of the same credential.
    seat = resolver.credential_agent() if actor.via == "cheese" else None
    author = seat or await TopicMemberService(db).resolve_agent_handle(
        place.room_id, room_id=place.room_id
    )
    declared = (body.get("as") or "").strip().lower()
    if declared == "app":
        # An app artifact points at the running server, not a file — the stored
        # content is a human note ("Vue dev server"), not a path.
        path = (body.get("path") or "app").strip()[:120]
        await _reject_unreachable_app(place.conversation_id, author)
    else:
        path = clean_artifact_path(body.get("path") or "")
    as_ = declared or artifact_kind_for(path)
    mime = ARTIFACT_MIME.get(as_)
    if mime is None:
        raise ValidationError(
            say(
                "artifactKindUnsupported",
                kind=repr(as_),
                allowed=listing(ARTIFACT_MIME),
            )
        )
    if as_ != "app" and ("content" in body or "content_b64" in body):
        # A remote machine's file is not in the backend worktree until published.
        # Office files and PDFs are not text, so they travel base64-encoded; a
        # caller that sends them as `content` would either fail to read them or
        # corrupt them on the way, which is why the two fields are separate
        # rather than one field that guesses.
        if "content_b64" in body:
            encoded = body["content_b64"]
            if not isinstance(encoded, str):
                raise ValidationError(say("contentB64MustBeText"))
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValidationError(say("contentB64Invalid")) from exc
        else:
            content = body["content"]
            if not isinstance(content, str):
                raise ValidationError(say("contentMustBeText"))
            raw = content.encode()
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ValidationError(
                say("artifactTooLarge", mb=MAX_ARTIFACT_BYTES // (1024 * 1024))
            )
    if as_ != "app" and ("content" in body or "content_b64" in body):
        # Through the draft history: the state this replaces stays restorable,
        # and `base_version` (the version `cheese pull` read) turns an overwrite
        # of somebody's newer save into a 409.
        base = body.get("base_version")
        note = body.get("note")
        await room_files.save_room_file(
            db,
            project_id=place.project_id,
            room_id=place.room_id,
            path=path,
            data=raw,
            author=author if actor.via == "cheese" else actor.handle,
            author_kind="agent" if actor.via == "cheese" else "human",
            source="ai" if actor.via == "cheese" else "upload",
            note=note if isinstance(note, str) else None,
            base_version=base if isinstance(base, str) and base else None,
        )
    block = await add_shown_block(
        db,
        project_id=place.project_id,
        conversation_id=place.conversation_id,
        path=path,
        author=author,
        mime=mime,
    )
    payload = block.model_dump(mode="json")
    await get_broker().publish(
        str(place.conversation_id), {"type": "assistant_block", "block": payload}
    )
    return ok(payload)


@router.get("/{topic_id}/shown")
async def list_shown(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """这个房间里摆出来过的东西 (#1085 结论四)。

    一个房间常有好几样值得看的东西，而「当前预览」只说得出最后那一样 —— 这里是全
    部，新的在前。它们仍然只属于这个房间；要成为项目的产物得有人按一下。"""
    place = await TopicService(db).place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    shown = await BlockRepository(db).shown_in_room(place.conversation_id)
    items = [
        {
            "path": block.content,
            "mime": block.mime_type,
            "kind": "app" if block.mime_type == ARTIFACT_MIME["app"] else "file",
            "shown_at": block.created_at.isoformat(),
        }
        for block in shown
    ]
    return ok(page(items, len(items)))


@router.post("/{topic_id}/shown/save")
async def save_shown_to_library(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """把房间里的这一份留进资料库 —— 只有人能按 (#1085 结论四)。

    一轮里铸出来的凭据在 `authorize_project` 那里只读得进来，所以 芝士 摆得出东西，
    却留不下它：这份东西以后还用不用得上，是人的判断。"""
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.require_verified_caller(project_id=place.project_id)
    await resolver.authorize_project(actor, project_id=place.project_id)
    # The file is the room's: only someone the room lets in copies it out.
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    name = await room_files.save_to_library(
        db,
        project_id=place.project_id,
        room_id=place.room_id,
        path=clean_artifact_path(str(body.get("path") or "")),
        by=actor.handle,
        said_in=place.inner_id,
    )
    await db.commit()
    return ok({"name": name})
