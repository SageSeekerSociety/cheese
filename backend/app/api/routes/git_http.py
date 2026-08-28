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

import os
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, Header, Request, Response

from app.core.errors import AuthenticationRequiredError, NotFoundError
from app.core.sandbox_auth import verify_scoped_token
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
    that is checked out. Topics here are jj workspaces, which git does not count
    as checked out, so today only the repo's own HEAD (the base branch) is
    exposed to this — but the rejection message goes to a push that CANNOT report
    it (a Stop hook must not take the turn down), so it would look exactly like
    success. ``updateInstead`` also refuses when the target worktree has
    uncommitted edits, so a human's unsaved work is never overwritten.
    """
    for key, value in (
        ("http.receivepack", "true"),
        ("receive.denyCurrentBranch", "updateInstead"),
    ):
        subprocess.run(
            ["git", "config", key, value], cwd=repo, capture_output=True, check=False
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
    response = await _cgi(repo, f"/{_RECEIVE}", request, await request.body())
    # Unconditionally, not only on success: jj is colocated here and does not see
    # a push on its own, and a push that only partly applied has still moved refs.
    # This keeps jj's view TRUE; it is not on its own what keeps the pushed work
    # on the branch. That is the worktree's job, and it does it by starting from
    # the branch and rebasing onto it (`_ensure_worktree`,
    # `_put_the_branch_under_the_working_copy`) — an import that merely told jj
    # where the branch was left the next bookmark move free to walk it back.
    # The import is idempotent and cheap, so there is nothing to gain by guessing.
    subprocess.run(
        ["jj", "git", "import"],
        cwd=repo,
        capture_output=True,
        check=False,
        timeout=30,
    )
    return response
