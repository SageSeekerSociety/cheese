"""Serve each project's repo over git's own protocol, so every device can
push what it wrote.

Every device owns an independent topic tree and pushes its branch back so 采纳
sees the work.

The transport has to be HTTP: the machine dials out (it is behind NAT) and it
already reaches this origin — that is where it downloaded its connector. So the
repo is served here, at the origin the machine already trusts, authorised by the
scoped token it already holds.

`git http-backend` does the protocol. Writing our own sync would mean inventing
history, deletes and conflict handling badly; with this the agent uses plain
`git push`.
"""

import asyncio
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, NotFoundError
from app.core.sandbox_auth import verify_scoped_token
from app.domain.room_task.models import TreeStatus
from app.domain.workspace import service as ws

router = APIRouter(prefix="/projects", tags=["git"])

# The three endpoints of git's smart HTTP protocol.
_UPLOAD = "git-upload-pack"  # clone / fetch
_RECEIVE = "git-receive-pack"  # push


def _backend_path() -> str:
    """Where git keeps http-backend, asked of git rather than assumed.

    It sits in libexec, and that path differs per distribution — hardcoding
    Debian's made this work only in the production image and nowhere else.
    """
    exec_path = subprocess.run(
        ["git", "--exec-path"], capture_output=True, text=True, check=False
    ).stdout.strip()
    return str(Path(exec_path or "/usr/lib/git-core") / "git-http-backend")


def _repo_for(project_id: uuid.UUID, token: str | None) -> Path:
    """The project's repo, once the caller has proved a claim on THIS project.

    The device's scoped token is project-bound, so a token for one project can
    never reach another's history — which matters more here than anywhere else,
    because this endpoint hands out the whole repository.
    """
    if not token or not verify_scoped_token(token, project_id=str(project_id)):
        raise AuthenticationRequiredError("git access needs this project's token")
    repo = ws.ensure_repo(project_id)
    if not (repo / ".git").exists():
        raise NotFoundError("project has no repository")
    _configure_for_push(repo)
    return repo


def _configure_for_push(repo: Path) -> None:
    """Make this repo accept a push from a machine.

    ``http.receivepack``: http-backend refuses to serve receive-pack otherwise.

    ``receive.denyCurrentBranch=updateInstead``: git rejects a push to a branch
    that is checked out, and every open topic is checked out here (its worktree
    sits on its branch, which is how the agent's own commit moves it). The
    rejection would go to a push that CANNOT report it — a Stop hook must not
    take the turn down — so it would look exactly like success while the work
    stayed on the machine. ``updateInstead`` lands the push and moves that
    worktree onto it, and still refuses when the worktree has uncommitted
    edits, so a human's unsaved work is never overwritten. The workspace layer
    sets the same thing when it creates a worktree; this covers a repo that
    predates it.
    """
    for key, value in (
        ("http.receivepack", "true"),
        ("receive.denyCurrentBranch", "updateInstead"),
    ):
        subprocess.run(
            ["git", "config", key, value], cwd=repo, capture_output=True, check=False
        )
    # A topic whose directory was deleted out of band — disk cleanup, an
    # operator, a wiped volume — leaves its branch registered to a worktree that
    # is not there, and `updateInstead` then fails trying to enter it: every
    # push to that branch is rejected, silently, forever. Pruning here costs a
    # directory scan and makes the dead entry stop mattering.
    subprocess.run(
        ["git", "worktree", "prune"], cwd=repo, capture_output=True, check=False
    )


async def _cgi(
    repo: Path, path_info: str, request: Request, body: bytes | None = None
) -> Response:
    """Run git-http-backend as CGI and translate its reply.

    The body is read whole rather than streamed: a project repo here is small,
    and streaming both ways through a subprocess is a lot of machinery to get
    subtly wrong. If repos grow, this is the place to revisit.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_PROJECT_ROOT": str(repo.parent),
        "GIT_HTTP_EXPORT_ALL": "1",
        "PATH_INFO": f"/{repo.name}{path_info}",
        "REQUEST_METHOD": request.method,
        "QUERY_STRING": request.url.query,
        "CONTENT_TYPE": request.headers.get("content-type", ""),
        "CONTENT_LENGTH": str(len(body or b"")),
        "REMOTE_USER": "cheese-device",
        # git refuses to run without an identity when receive-pack creates a
        # reflog entry.
        "GIT_COMMITTER_NAME": "芝士",
        "GIT_COMMITTER_EMAIL": "cheese@zhishi.local",
    }
    encoding = request.headers.get("content-encoding")
    if encoding:
        env["HTTP_CONTENT_ENCODING"] = encoding

    process = subprocess.run(
        [_backend_path()],
        input=body or b"",
        capture_output=True,
        env=env,
        timeout=120,
    )
    if process.returncode != 0:
        return Response(
            content=process.stderr[:500] or b"git backend failed",
            status_code=500,
            media_type="text/plain",
        )

    # CGI: headers, blank line, body.
    head, _, payload = process.stdout.partition(b"\r\n\r\n")
    if not _:
        head, _, payload = process.stdout.partition(b"\n\n")
    status = 200
    headers: dict[str, str] = {}
    for line in head.decode(errors="replace").splitlines():
        name, sep, value = line.partition(":")
        if not sep:
            continue
        name, value = name.strip(), value.strip()
        if name.lower() == "status":
            status = int(value.split()[0])
        else:
            headers[name] = value
    media = headers.pop("Content-Type", "application/octet-stream")
    return Response(
        content=payload, status_code=status, headers=headers, media_type=media
    )


@router.get("/{project_id}/git/branch/{topic_id}")
@router.post("/{project_id}/git/branch/{topic_id}")
async def branch_for_place(
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    on: str = "",
    heads: str = Body(default="", media_type="text/plain"),
    x_cheese_token: str | None = Header(default=None, alias="X-Cheese-Token"),
) -> dict:
    """Which branch this place writes to **right now**, and how to get onto it.

    A device's screen is long-lived and its environment is fixed at launch, so
    `CHEESE_GIT_BRANCH` is a snapshot of the batch that was open when the screen
    started. A room delivers, the batch merges, the next one opens on a new
    branch — and the screen keeps pushing everything it does onto the branch
    that already landed, silently, for as long as it lives. That is not a
    hypothetical: this repository's own room did exactly that.

    So the branch is ASKED FOR at push time instead of remembered. Sitting in
    `git_http` because it belongs to the same conversation and the same
    credential as the push it precedes: the device already holds a
    project-scoped token and already talks to this router to push.

    `on` is the branch the caller's clone is currently on and `heads` are the
    commits it has (its HEAD and that commit's ancestors). Both are asked for
    because **the name alone is not evidence**: `git branch -m` renames a branch
    without moving a single commit, so a clone whose history is built on a batch
    already squashed into main can present a name this room never delivered from
    — and be told, truthfully about the name and disastrously about the history,
    that there is nothing to carry over. The commits cannot be renamed, so they
    are what the batch is identified by; `on` only ADDS the batch this device's
    own record says it last published to.

    The three extra facts in the answer exist because a device **cannot work
    them out for itself** after a squash merge:

    - `on_merged` — is this clone's history built on a batch of THIS room that
      has landed? Ancestry cannot say. A squash commit is not a descendant of
      the branch it squashed, so `merge-base --is-ancestor` answers "no" for a
      batch that is fully delivered and "no" for one that never was. Nor can a
      device tell a landed batch from a branch that was never a batch at all
      (its own `dev/…`, or the base branch it was cloned onto) — and the two
      need opposite handling: the first must be carried onto the new batch or
      refused, the second is just a name and pushes normally.
    - `on_branch` — which batch that was, by name. The device keys its own
      records (what it published, where it was locally when it did) by branch
      name, so being told the name is what lets it find them after a rename.
    - `on_head` — the commit that batch delivered, empty when the merge
      predates recording it. A merged batch with no `on_head` is the case the
      device must REFUSE rather than push: it knows the work here sits on a
      landed batch and cannot tell where that batch ends.
    - `base` / `base_sha` — the commit the next batch starts from, by name AND
      by sha. Same reason: the delivering clone has no ref that reaches it.

    All of them are the platform stating a fact it alone holds, so that a device
    grafts its next batch onto the right commit only when the previous one is
    confirmed delivered — never on a guess.
    """
    _repo_for(project_id, x_cheese_token)
    from app.domain.room_task.place import PlaceResolver
    from app.domain.room_task.services import WorkTreeService
    from app.domain.topic.models import Topic

    # BEFORE resolving. The token proves a claim on the project in the URL and
    # NOTHING about the topic, and a place id is resolved globally — so without
    # this a device holding one project's credential could name any other
    # project's room and be told which branch it is writing to.
    #
    # And it has to come BEFORE rather than after, because resolving a place is
    # not a pure read: it repairs that place's on-disk tree marker
    # (`PlaceResolver._heal_the_marker`). Checking afterwards would refuse the
    # request having already written into the very project it is refusing to
    # talk about. Same answer for "no such topic" and "somebody else's topic" —
    # which of the two it is, is exactly what a caller probing ids wants told.
    room = await db.get(Topic, topic_id)
    if room is None or room.project_id != project_id:
        raise NotFoundError("这个项目里没有这个地点")
    place = await PlaceResolver(db).resolve(topic_id)
    if place is None:
        raise NotFoundError("这个项目里没有这个地点")
    if place.branch_name is None:
        raise NotFoundError("这个地点现在没有可写的分支")
    base, base_sha = await asyncio.to_thread(ws.base_branch_head, project_id)
    history = await WorkTreeService(db).history(place.room_id)
    landed = {
        ws.branch_for_tree(t.id): t for t in history if t.status == TreeStatus.merged
    }
    carried = set()
    reported = _commits_a_clone_reported(heads)
    if landed and reported:
        carried = await asyncio.to_thread(
            ws.batches_a_clone_stands_on,
            project_id,
            {b: (t.delivered_head or "") for b, t in landed.items()},
            reported,
        )
    # 名字命中和提交命中放在一起挑，挑最后交付的那一批。A clone that has grafted
    # already still has the batch BEFORE last in its ancestry — HEAD never moves
    # here, the graft is built with plumbing — so on a third batch the commits
    # identify the first one while `on` (the syncer's own record of what it last
    # published to) identifies the second. Carrying onto the older of the two
    # would re-deliver everything the second one added, so the newest wins.
    was = max(
        (t for b, t in landed.items() if b == on or b in carried),
        key=lambda t: t.merged_at or t.created_at,
        default=None,
    )
    # 「交出去的是哪个 commit」来自合并那一刻记下的 `delivered_head`，**不是**
    # 那条分支现在指向哪里。A device grafting its next batch does a three-way
    # merge whose BASE is the content that was delivered; the branch is
    # mutable, so a commit pushed onto it after the merge (a stale screen,
    # a hand push) would be taken for delivered content it never was, and
    # the graft would silently re-deliver or drop work. A batch that merged
    # before this was recorded answers `on_merged` with no `on_head`, and
    # the device refuses rather than guesses.
    return ok(
        {
            "branch": place.branch_name,
            "tree_id": str(place.tree_id),
            "base": base,
            "base_sha": base_sha,
            "on_merged": was is not None,
            "on_branch": ws.branch_for_tree(was.id) if was is not None else "",
            "on_head": (was.delivered_head or "") if was is not None else "",
        }
    )


# Only full hex object names count as reported commits.
_A_COMMIT = re.compile(r"\A[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


def _commits_a_clone_reported(heads: str) -> list[str]:
    return [c for c in heads.split() if _A_COMMIT.match(c)]


@router.get("/{project_id}/git/info/refs")
async def info_refs(
    project_id: uuid.UUID,
    request: Request,
    x_cheese_token: str | None = Header(default=None, alias="X-Cheese-Token"),
) -> Response:
    """The advertisement a clone/push starts with."""
    repo = _repo_for(project_id, x_cheese_token)
    return await _cgi(repo, "/info/refs", request)


@router.post("/{project_id}/git/git-upload-pack")
async def upload_pack(
    project_id: uuid.UUID,
    request: Request,
    x_cheese_token: str | None = Header(default=None, alias="X-Cheese-Token"),
) -> Response:
    """Clone / fetch."""
    repo = _repo_for(project_id, x_cheese_token)
    return await _cgi(repo, f"/{_UPLOAD}", request, await request.body())


@router.post("/{project_id}/git/git-receive-pack")
async def receive_pack(
    project_id: uuid.UUID,
    request: Request,
    x_cheese_token: str | None = Header(default=None, alias="X-Cheese-Token"),
) -> Response:
    """Push — the direction that was missing."""
    repo = _repo_for(project_id, x_cheese_token)
    return await _cgi(repo, f"/{_RECEIVE}", request, await request.body())
