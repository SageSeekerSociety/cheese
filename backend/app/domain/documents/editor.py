"""Editing a room file in the office editor (OnlyOffice Document Server).

The editor works on the Office file itself — the `.docx`, `.xlsx`, `.pptx` a
room already holds — so there is no internal format to import into or export
out of: what a person saves is the file 芝士 reads next, and 「导出」 is
downloading it. That is the reason for this engine over a rich-text editor: a
report pasted into an HTML editor and written back as Word loses the styles,
numbering and charts the file was made of, silently.

Three parties and two kinds of token:

- The browser asks the backend for a config (`editor_config`). The config is
  signed with the secret shared with the editor, which is what the editor
  checks before it opens anything.
- The editor fetches the document and later posts saves back to URLs carrying
  a *link token* the backend signed for this one file, version and person. Those
  endpoints take no other credential, because the editor has none of ours.
- A save callback is itself signed by the editor with the shared secret; an
  unsigned or wrongly signed callback is refused, so nobody can post bytes into
  a room by guessing a URL.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import jwt

from app.core.config import settings

#: What the editor can open, and as which of its three editors. Only the Office
#: Open XML formats are editable: the editor can open the older ones, but saving
#: would hand back a different format under the old name.
_KINDS = {
    "docx": "word",
    "xlsx": "cell",
    "pptx": "slide",
}
_VIEW_ONLY = {
    "doc": "word",
    "odt": "word",
    "rtf": "word",
    "xls": "cell",
    "ods": "cell",
    "csv": "cell",
    "ppt": "slide",
    "odp": "slide",
}

_LINK_TTL = 12 * 3600


class EditorUnavailable(RuntimeError):
    """This deployment has no editor configured."""


class EditorRefused(RuntimeError):
    """A request that claims to come from the editor could not prove it."""


@dataclass(frozen=True)
class Link:
    project_id: uuid.UUID
    room_id: uuid.UUID
    path: str
    version: str | None
    handle: str
    key: str


def enabled() -> bool:
    return bool(settings.office_editor_jwt_secret)


def suffix(path: str) -> str:
    return PurePosixPath(path).suffix.lower().lstrip(".")


def editable(path: str) -> bool:
    return suffix(path) in _KINDS


def document_type(path: str) -> str | None:
    ext = suffix(path)
    return _KINDS.get(ext) or _VIEW_ONLY.get(ext)


def document_key(room_id: uuid.UUID, path: str, version: str | None) -> str:
    """One key per (file, content). The editor treats a key as one document:
    people who open the same key edit together, and a new key is a fresh load —
    which is exactly what a new version of the file should be."""
    ident = hashlib.sha256(f"{room_id}:{path}".encode()).hexdigest()[:24]
    return f"{ident}-{version or 'new'}"


def _secret() -> str:
    if not settings.office_editor_jwt_secret:
        raise EditorUnavailable("这个部署没有启用在线编辑")
    return settings.office_editor_jwt_secret


def _link_secret() -> str:
    # Not the shared secret itself: a link token must not double as something
    # the editor would accept as its own config.
    return hashlib.sha256(f"{_secret()}:cheese-office-link".encode()).hexdigest()


def sign_link(link: Link) -> str:
    return jwt.encode(
        {
            "p": str(link.project_id),
            "r": str(link.room_id),
            "path": link.path,
            "v": link.version,
            "u": link.handle,
            "k": link.key,
            "exp": int(time.time()) + _LINK_TTL,
        },
        _link_secret(),
        algorithm="HS256",
    )


def read_link(token: str) -> Link:
    try:
        claims = jwt.decode(token, _link_secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise EditorRefused("编辑链接无效或已过期") from exc
    return Link(
        project_id=uuid.UUID(claims["p"]),
        room_id=uuid.UUID(claims["r"]),
        path=claims["path"],
        version=claims.get("v"),
        handle=claims["u"],
        key=claims["k"],
    )


def editor_config(
    *,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    path: str,
    version: str | None,
    handle: str,
    display_name: str,
    can_edit: bool,
) -> dict:
    """What the browser hands to `new DocsAPI.DocEditor(...)`, signed."""
    kind = document_type(path)
    if kind is None:
        raise ValueError(f"the editor does not open .{suffix(path)}")
    key = document_key(room_id, path, version)
    link = sign_link(
        Link(
            project_id=project_id,
            room_id=room_id,
            path=path,
            version=version,
            handle=handle,
            key=key,
        )
    )
    backend = settings.office_editor_backend_url.rstrip("/")
    edit = can_edit and editable(path)
    config: dict = {
        "document": {
            "fileType": suffix(path),
            "key": key,
            "title": PurePosixPath(path).name,
            "url": f"{backend}/office-editor/files/{link}",
            "permissions": {
                "edit": edit,
                "download": True,
                "print": True,
                "comment": edit,
                "review": edit,
            },
        },
        "documentType": kind,
        "editorConfig": {
            "callbackUrl": f"{backend}/office-editor/callback/{link}",
            "lang": "zh-CN",
            "region": "zh-CN",
            "mode": "edit" if edit else "view",
            "user": {"id": handle, "name": display_name or handle},
            "customization": {
                "forcesave": True,
                "autosave": True,
                "compactHeader": True,
                "hideRightMenu": False,
                "uiTheme": "theme-light",
            },
        },
    }
    config["token"] = jwt.encode(config, _secret(), algorithm="HS256")
    return config


def verify_callback(body: dict, authorization: str | None) -> dict:
    """The callback's payload, once the editor's signature on it checks out."""
    token = body.get("token")
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
    if not token:
        raise EditorRefused("回调没有签名")
    try:
        claims = jwt.decode(token, _secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise EditorRefused("回调签名不对") from exc
    # Header-carried tokens wrap the body as `payload`.
    return claims.get("payload", claims)


def internal_download_url(url: str) -> str:
    """Where the backend fetches a saved document.

    The editor names the result at the address the *browser* reached it by,
    which from inside the deployment may not route anywhere. Only the path is
    taken from it — the host is always our own editor, so a callback cannot
    point the backend at an arbitrary address.
    """
    parts = urlsplit(url)
    at = parts.path.find("/cache/files/")
    if at < 0:
        raise EditorRefused("回调给的下载地址不是编辑器的")
    tail = parts.path[at:]
    query = f"?{parts.query}" if parts.query else ""
    return settings.office_editor_internal_url.rstrip("/") + tail + query
