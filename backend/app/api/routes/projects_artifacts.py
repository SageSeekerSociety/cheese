"""A project's deliverables: the artifact list, one entry, and how it changes.

Part of #2143. First slice of `app/api/routes/projects.py`, following the same
pattern #2198/#2204/#2208/#2214 took for `spaces.py` and #2215 for
`topics.py`. projects.py is 1,651 lines against a 1,500-line cap that only
ratchets down.

What moves, verbatim:

  GET    /projects/{project_id}/artifacts
  GET    /projects/{project_id}/artifacts/{artifact_id}
  GET    /projects/{project_id}/artifacts/{artifact_id}/compare
  GET    /projects/{project_id}/artifacts/{artifact_id}/versions/{card_id}/file
  PATCH  /projects/{project_id}/artifacts/{artifact_id}
  POST   /projects/{project_id}/artifacts/{artifact_id}/merge
  DELETE /projects/{project_id}/artifacts/{artifact_id}

and the two helpers only they call: `_artifact_keeper` (who may change the
list -- a person, not a turn's credential) and `_artifact_ref` (the merge
target). They are one group because they are one subject read one way -- the
产物清单: what a project has delivered, one item's page and history, the bytes
it shipped, and the three things a person does to an item (rename it, merge
two that were always one, take it off). The list is read-only by design: it
grows out of delivery, so there is no POST.

What stays behind, and why. Nothing this block imports belongs to it alone.
`ProjectService`, `readable_rooms`, `ok`/`page`,
`ActorResolverDep` and the error types are all read by handlers that stay, so
each is imported here from the module that owns it (`app.domain.project.services`,
`app.api.place`, `app.api.response`, `app.api.auth`, `app.core.errors`) rather
than re-exported through projects.py. `DbSession` is the one exception: it is
projects.py's own alias for the session dependency, and it comes from there the
way `topics_tasks.py` takes its own from topics.py.

No frozen edge moves, no contract grows. The block reads no domain *model*
module (C2 freezes `app.api.routes.projects -> app.domain.*.models`, and no
import of that shape is in it) and no *repository* module (the ratchet in
`tests/unit/test_domain_import_guard.py` freezes four repository pairs against
`app.api.routes.projects`; all four stay, because the handlers that read those
repositories stay too). `app.domain.project.artifacts`, `...preview.office`,
`...library.service`, `...documents.text` and `...repository.forge_files` are
modules and services, not repositories, so importing them here adds nothing to
either ratchet and `.importlinter` is untouched.

Ordering. This module sorts after `projects.py` (`_` > `.`) and before
`push.py` (`projects_` < `push`), so its paths mount later in the route table
than they did inside projects.py -- no longer right after `/default-agent`,
but after every path that stays, up to `/upstream`. Every moved
path carries a literal `artifacts` where each route that now precedes it
carries a literal of its own, and no route registered in between has a
parameter in that segment, so none loses its first full match; resolving every
path in the table confirms each still reaches the handler it did before, now
under `app.api.routes.projects_artifacts`. OpenAPI is byte-identical apart from
the moved paths' position in the paths object.

The new module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`, so the declaration below, with
the same prefix and tags, is all it takes.
"""

import asyncio
import uuid
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import Response

from app.api.auth import ActorResolverDep
from app.api.place import channels_unseen, readable_rooms
from app.api.response import ok, page
from app.api.routes.projects import DbSession
from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.documents.text import delivered_comparison
from app.domain.library import service as library
from app.domain.preview import office
from app.domain.project import artifacts
from app.domain.project.services import ProjectService
from app.domain.repository.forge_files import ProjectFiles

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("/{project_id}/artifacts")
async def list_artifacts(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """产物清单：这个项目交出去的东西，一项一行 (#1085 结论二、三)。

    清单只读，而且没有配套的新建入口：它由交付长出来 —— 交出去一次合并的，落在项目
    那个仓库那一项上（平台自己认）；交出去一份文件或一个地址的，递卡时点名的名字不
    在清单上就当场多一项。所以这里没有 POST，不是还没做。"""
    await ProjectService(db).get_or_404(project_id)
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    rows = await artifacts.list_for_project(
        db, project_id, hidden=await channels_unseen(db, resolver, actor, project_id)
    )
    items = [
        {
            "id": str(a.id),
            "name": a.name,
            "about": a.about,
            "version": a.version,
            "delivered_at": a.delivered_at.isoformat() if a.delivered_at else None,
        }
        for a in rows
    ]
    return ok(page(items, len(items)))


@router.get("/{project_id}/artifacts/{artifact_id}")
async def read_artifact(
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """清单上这一项自己的那一页 (#1085 结论二)：现在是第几版，以及交付过的每一版。

    一版就是一张采纳了的卡，所以这里没有「版本表」——历史是数出来的，撤回一次采
    纳，它后面几版的号自己往前挪。"""
    await ProjectService(db).get_or_404(project_id)
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    hidden = await channels_unseen(db, resolver, actor, project_id)
    row = await _artifact_seen(db, project_id, artifact_id, hidden)
    listed = await artifacts.summary(db, row.id, hidden=hidden)
    history = await artifacts.versions(db, row.id, hidden=hidden)
    # 每一版出自哪个房间，能点回去；读不了的房间不写名字。
    rooms = await readable_rooms(db, resolver, actor, project_id)
    return ok(
        {
            "id": str(row.id),
            "name": row.name,
            "about": row.about,
            "version": listed.version if listed else 0,
            "delivered_at": (
                listed.delivered_at.isoformat()
                if listed and listed.delivered_at
                else None
            ),
            "versions": [
                artifacts.version_payload(project_id, v, rooms) for v in history
            ],
        }
    )


@router.get("/{project_id}/artifacts/{artifact_id}/compare")
async def compare_artifact_versions(
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    before: uuid.UUID,
    after: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    await ProjectService(db).get_or_404(project_id)
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    hidden = await channels_unseen(db, resolver, actor, project_id)
    await _artifact_seen(db, project_id, artifact_id, hidden)
    history = {
        v.card_id: v for v in await artifacts.versions(db, artifact_id, hidden=hidden)
    }
    if before not in history or after not in history:
        raise NotFoundError(say("deliveryVersionsMissing"))
    left, right = history[before], history[after]
    result = {
        "kind": "unavailable",
        "identical": None,
        "files": [],
        "note": "unavailable",
    }
    if left.kind == right.kind == "file" and left.filename and right.filename:
        old = library.read_artifact_snapshot(project_id, before, left.filename)
        new = library.read_artifact_snapshot(project_id, after, right.filename)
        comparison = await asyncio.to_thread(
            delivered_comparison, old, new, left.filename, right.filename
        )
        result = {
            "kind": "file",
            "identical": comparison["identical"],
            "files": [{"path": right.filename, **comparison}],
            "note": None,
        }
    elif left.kind == right.kind == "merge" and left.revision and right.revision:
        changes = await ProjectFiles(db, project_id, None).compare_revisions(
            left.revision, right.revision
        )
        result = {
            "kind": "merge",
            "identical": not changes,
            "files": changes,
            "note": "source",
        }
    elif left.kind == right.kind == "link":
        result = {
            "kind": "link",
            "identical": left.url == right.url,
            "files": [],
            "note": "link",
        }
    return ok(result)


@router.get("/{project_id}/artifacts/{artifact_id}/versions/{card_id}/file")
async def download_artifact_version(
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    card_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    preview_pdf: bool = False,
) -> Response:
    """这一版交出去的那一份字节 (#1085 结论五)。

    取的是当时交出去的那个快照，不是现在从源重建一次的结果：半年之后依赖变了、字
    体没了，重建出来的可能和当时交出去的不是同一份东西，而用户要的是他交出去的那
    一份。"""
    await ProjectService(db).get_or_404(project_id)
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    hidden = await channels_unseen(db, resolver, actor, project_id)
    await _artifact_seen(db, project_id, artifact_id, hidden)
    version = next(
        (
            v
            for v in await artifacts.versions(db, artifact_id, hidden=hidden)
            if v.card_id == card_id
        ),
        None,
    )
    if version is None:
        raise NotFoundError(say("itemVersionMissing"))
    if version.kind != "file" or not version.filename:
        # 交出去的是一个地址、或者一次合并：没有可下载的文件，而这不是缺东西。
        raise NotFoundError(say("versionNotAFile"))
    data = library.read_artifact_snapshot(project_id, card_id, version.filename)
    if preview_pdf:
        data = await office.preview_pdf(data, version.filename)
    filename = quote(version.filename, safe="")
    return Response(
        content=data,
        media_type="application/pdf" if preview_pdf else "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, max-age=3600",
        },
    )


async def _artifact_keeper(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> set[uuid.UUID]:
    """改清单的只有人。

    一轮里铸出来的凭据在 `authorize_project` 那里只读得进来，所以 芝士 改不了、
    合不了、删不了清单上的东西 —— 它只能在交付时声明，而「这两项是不是同一个东西」
    「这个名字对不对」正是要人判断的那部分。"""
    await ProjectService(db).get_or_404(project_id)
    actor = await resolver.require_verified_caller(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    return await channels_unseen(db, resolver, actor, project_id)


async def _artifact_seen(
    db: DbSession,
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    hidden: set[uuid.UUID],
):
    """清单上这一项，对读的人而言：只在他不在的私密频道里交付过的一项，和一个不
    存在的 id 答得一模一样。"""
    row = await artifacts.get_or_404(db, project_id=project_id, artifact_id=artifact_id)
    if await artifacts.kept_from(db, row.id, hidden):
        raise NotFoundError(say("artifactNotFound"))
    return row


@router.patch("/{project_id}/artifacts/{artifact_id}")
async def rename_artifact(
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """给清单上这一项换个名字。

    卡指着的是这一行的 id，所以改名之后，之前的每一次交付照样算这一项的版本 ——
    名字起错了的正解是改名，不是删掉重来。"""
    hidden = await _artifact_keeper(project_id, db, resolver)
    row = await _artifact_seen(db, project_id, artifact_id, hidden)
    renamed = await artifacts.rename(db, row, name=str(body.get("name") or ""))
    await db.commit()
    return ok({"id": str(renamed.id), "name": renamed.name})


@router.post("/{project_id}/artifacts/{artifact_id}/merge")
async def merge_artifact(
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """这两项其实是同一个东西：把这一项的交付都算到 `into` 那一项上。

    留哪个名字是人的判断，所以方向由调用方给，平台不挑。"""
    hidden = await _artifact_keeper(project_id, db, resolver)
    source = await _artifact_seen(db, project_id, artifact_id, hidden)
    target = await _artifact_seen(
        db, project_id, _artifact_ref(body.get("into")), hidden
    )
    kept = await artifacts.merge(db, source=source, target=target)
    await db.commit()
    return ok({"id": str(kept.id), "name": kept.name})


@router.delete("/{project_id}/artifacts/{artifact_id}")
async def delete_artifact(
    project_id: uuid.UUID,
    artifact_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """把这一项从清单上去掉 —— 用户说它本来就不该是一项。

    声明过它的那些卡留在原处，只是不再指向任何一项：那些交付确实发生过。"""
    hidden = await _artifact_keeper(project_id, db, resolver)
    row = await _artifact_seen(db, project_id, artifact_id, hidden)
    await artifacts.delete(db, row)
    await db.commit()
    return ok({"deleted": True})


def _artifact_ref(raw: object) -> uuid.UUID:
    """合并的目标 —— 清单上另一项的 id。"""
    try:
        return uuid.UUID(str(raw or ""))
    except ValueError as exc:
        raise ValidationError(say("mergeIntoOtherItem")) from exc
