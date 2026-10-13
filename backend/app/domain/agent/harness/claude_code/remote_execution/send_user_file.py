"""`SendUserFile`: hand the files a tool named to this room.

Split out of `client.py` whole — that module sits over its size cap and may only
shrink. It reaches nothing private: the two file readers come from
`executor_transport`, and the room calls arrive as the `client` argument.
Shipped beside `client.py` (`release.sources`).
"""

from __future__ import annotations

import base64
import os
import uuid
from typing import Any

if __package__:
    from app.domain.agent.executor_transport import (
        read_file_on_the_machine,
        session_path,
        stat_file_on_the_machine,
    )
else:
    from executor_transport import (
        read_file_on_the_machine,
        session_path,
        stat_file_on_the_machine,
    )


MAX_SEND_USER_FILE_BYTES = 10 * 1024 * 1024

# What the tool result promises the caller about the file: `isImage` for the
# suffixes that are pictures, `media_type` for what the bytes are. The room
# types the artifact off the path on `POST /topics/{id}/shown`
# (`room_files.ARTIFACT_MIME`); this tool never declares `as`, so that table is
# the only one that names a kind. These two fields describe the file to the
# caller — they are not a second copy of the room's kind table.
_SEND_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_SEND_MEDIA_TYPE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def send_user_file_paths(path, config):
    """`(executor path, room path)` for one file a SendUserFile named.

    The model spells paths the way the session was told to (the executor's
    workspace); the room wants a workspace-relative pointer because
    `POST /topics/{id}/shown` refuses absolute ones. A file that lives outside
    the workspace keeps its name under `uploads/`, the address this room already
    gives a paste with no name of its own.
    """
    path = (path or "").replace("\\", "/")
    work = session_path(config.get("workspace") or "").rstrip("/")
    machine = path
    if work and not machine.startswith("/"):
        # The executor's shell keeps its own cwd across commands; a relative
        # path is only unambiguous once it is anchored at the workspace.
        machine = work + "/" + machine
    name = machine.rsplit("/", 1)[-1] or "file"
    rel = machine
    if work and (machine == work or machine.startswith(work + "/")):
        rel = machine[len(work) :].lstrip("/")
    parts = rel.split("/") if rel else []
    if not rel or rel.startswith("/") or ".." in parts or ".git" in parts:
        rel = f"uploads/{uuid.uuid4().hex}/{name}"
    return machine, rel


def send_user_file_entry(path, name, size, upload_error=None):
    suffix = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    entry = {
        "path": path,
        "size": size,
        "isImage": suffix in _SEND_IMAGE_SUFFIXES,
        "media_type": _SEND_MEDIA_TYPE.get(suffix, "application/octet-stream"),
        "pathValidated": upload_error is None,
    }
    if upload_error is not None:
        entry["upload_error"] = upload_error
    return entry


def send_user_file_body(raw):
    """The `POST /topics/{id}/shown` body for one file's bytes.

    `content_b64` for every kind, not just the binary ones: the room route
    already takes office and PDF bytes that way, and a caller that decoded to
    text first cannot carry them. The kind is not declared here — the route
    reads it off `path`, so this and the room's table cannot drift.
    """
    return {"content_b64": base64.b64encode(raw).decode("ascii")}


def deliver_send_user_file(client, config, payload, args, invoke):
    """Hand each file to this room, and the caption beside them.

    Delivery is `POST /topics/{id}/shown` — the route `cheese show` already
    publishes through (SKILL.md), which lands an artifact the room renders and
    offers for download. `POST /topics/{id}/attachments` is the input bar's
    staging area: a file parked there is waiting on a message that never comes.

    Each result entry's `path` is the file's resolved filesystem path (the
    executor path `send_user_file_paths` computes), what `SendUserFile` promises
    the caller. The room-relative pointer is the `path` on the POST body — a
    different field, for a different reader.
    """
    topic = os.environ.get("CHEESE_TOPIC", "")
    if not topic:
        raise RuntimeError("Delivering a file requires room credentials")
    attachments = []
    delivered = False
    for index, item in enumerate(args.get("files") or []):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise RuntimeError(
                "SendUserFile cannot deliver a pre-resolved "
                "{file_uuid, file_name, size, is_image} object; "
                "pass a file path instead"
            )
        path = str(item.get("path") or "")
        name = str(item.get("name") or "") or (
            path.replace("\\", "/").rsplit("/", 1)[-1] or "file"
        )
        machine, rel = send_user_file_paths(path, config)
        preexisting = item.get("upload_error")
        if preexisting:
            attachments.append(
                send_user_file_entry(machine, name, 0, upload_error=str(preexisting))
            )
            continue
        try:
            if item.get("data_b64"):
                raw = base64.b64decode(item["data_b64"], validate=True)
            else:
                # The plugin's stat never saw this file; refuse an oversize one
                # before `base64` walks it across the executor connection.
                size = stat_file_on_the_machine(
                    invoke, machine, f"{payload['id']}-file-{index}-stat"
                )
                if size > MAX_SEND_USER_FILE_BYTES:
                    attachments.append(
                        send_user_file_entry(
                            machine,
                            name,
                            size,
                            upload_error=(
                                f"文件太大（上限 "
                                f"{MAX_SEND_USER_FILE_BYTES // (1024 * 1024)}MB）"
                            ),
                        )
                    )
                    continue
                raw = read_file_on_the_machine(
                    invoke, machine, f"{payload['id']}-file-{index}"
                )
        except Exception as exc:
            attachments.append(
                send_user_file_entry(machine, name, 0, upload_error=str(exc))
            )
            continue
        if not raw:
            attachments.append(
                send_user_file_entry(machine, name, 0, upload_error="空文件")
            )
            continue
        if len(raw) > MAX_SEND_USER_FILE_BYTES:
            attachments.append(
                send_user_file_entry(
                    machine,
                    name,
                    len(raw),
                    upload_error=(
                        f"文件太大（上限 "
                        f"{MAX_SEND_USER_FILE_BYTES // (1024 * 1024)}MB）"
                    ),
                )
            )
            continue
        try:
            client.platform_request(
                {
                    "method": "POST",
                    "path": f"/topics/{topic}/shown",
                    "body": {"path": rel, **send_user_file_body(raw)},
                }
            )
        except Exception as exc:
            attachments.append(
                send_user_file_entry(machine, name, len(raw), upload_error=str(exc))
            )
            continue
        attachments.append(send_user_file_entry(machine, name, len(raw)))
        delivered = True
    caption = args.get("caption")
    if delivered and isinstance(caption, str) and caption.strip():
        client.publish_message(payload, {"content": caption.strip()})
    result: dict[str, Any] = {"attachments": attachments}
    if isinstance(caption, str):
        result["caption"] = caption
    if isinstance(args.get("display"), str):
        result["display"] = args["display"]
    return {"value": result}
