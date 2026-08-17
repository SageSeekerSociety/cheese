"""Serve locally-stored uploads — the URLs `STORAGE_TYPE=local` hands out.

`LocalStorageBackend` returns `{storage_local_url}/{key}` (default `/uploads/…`)
and that string is what lands in `material.url`, in a 赛题's inline images, and
in every page that renders them. Nothing served it: the backend had no such
route and the frontend image's nginx has no such `location`, so on a container
deployment every 素材 and every inline image was a 404. (`deploy/…-design.md`
describes the frontend proxying `/uploads` to the backend; the shipped
`nginx.conf` never did. Serving it here works under any gateway instead of
depending on one being configured right — the browser asks for `/api/uploads/…`,
the existing `/api/` block strips one segment, and it arrives.)

**Why this is not `StaticFiles`.** `type=file` uploads accept any `text/*` mime
(`routes/materials.py`), so a user can store HTML. Served inline from the app's
own origin, that is stored XSS against a frontend that keeps its JWT in
localStorage. So every response here is sent inert: a content type that cannot
execute for the dangerous extensions, `nosniff` so the browser will not go
looking for a better one, and a CSP that denies everything and sandboxes the
document. Images — the whole point of these URLs — are unaffected.

**Access model, unchanged and stated plainly:** these URLs are public. They were
designed that way (the S3 backend hands out public object URLs for the same
keys), and they are embedded in `<img>` tags that carry no Authorization header,
so a token check here would simply stop them rendering. The only protection is
that a key is unguessable (`uuid4().hex[:12]` plus a date path). This route does
not change that; it makes the existing design work. Anything that must be
private belongs behind `/attachments/{id}/download`, which does check.
"""

import mimetypes
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.errors import NotFoundError

router = APIRouter(tags=["uploads"])

# Types a browser will happily execute in the origin that served them. Anything
# on this list goes out as an opaque download instead of as itself — an uploaded
# .svg is still a perfectly good file, it just stops being a script host.
_NEVER_INLINE = {
    ".html",
    ".htm",
    ".xhtml",
    ".shtml",
    ".mhtml",
    ".svg",
    ".xml",
    ".xsl",
    ".js",
    ".mjs",
}

# Deny every capability an HTML document could ask for, and sandbox it besides,
# so even a mis-typed response cannot reach the session it was served next to.
_INERT_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}


def _resolved_root() -> Path:
    return Path(settings.storage_local_path).resolve()


def _safe_path(key: str) -> Path:
    """The file for `key`, or a 404 — never a path outside the storage root.

    `resolve()` collapses `..` and follows symlinks BEFORE the containment test,
    so neither a traversal in the URL nor a symlink planted in the storage dir
    can point out of the tree.
    """
    root = _resolved_root()
    candidate = (root / key).resolve()
    if candidate != root and root not in candidate.parents:
        raise NotFoundError("文件不存在")
    if not candidate.is_file():
        raise NotFoundError("文件不存在")
    return candidate


async def serve_upload(key: str) -> FileResponse:
    path = _safe_path(key)
    suffix = path.suffix.lower()
    if suffix in _NEVER_INLINE:
        media_type = "application/octet-stream"
    else:
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, headers=_INERT_HEADERS)


# Registered at exactly the path the storage backend hands out, so the two can
# never drift — and only when this deployment is the one serving those files. On
# S3 the URLs are absolute and point elsewhere; claiming a local route for them
# would answer 404 at a path nobody asks for while implying we serve it.
if settings.storage_type == "local" and settings.storage_local_url.startswith("/"):
    router.add_api_route(
        f"{settings.storage_local_url.rstrip('/')}/{{key:path}}",
        serve_upload,
        methods=["GET"],
        include_in_schema=False,
    )
