"""The files a turn hands a person in the room.

`send_user_file`, the platform tool this session serves, names files that are
on the executor, not on this host: this reads them there, or takes the bytes a
tool that showed an image already carries, and posts what the room's message
holds. Shipped beside `client.py` (`release.sources`), whose `transport`
delivers them for the `send_user_file` tool.
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
#: One message carries at most this many pictures (`ATTACHMENTS_PER_MESSAGE` in
#: `topics_messages`); the tool says so instead of letting the backend drop the
#: rest without a word.
MAX_CHAT_SEND_ATTACHMENTS = 9

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


def send_user_file_paths(path, config, anchor=None):
    """`(executor path, room path)` for one file a SendUserFile named.

    A relative path is the session's own: it is anchored at where the session
    started (`anchor`, `RemoteClient.working_directory`), which for a task is
    its checkout, not the project view. The room wants a workspace-relative
    pointer because `POST /topics/{id}/shown` refuses absolute ones. A file that
    lives outside the workspace keeps its name under `uploads/`, the address
    this room already gives a paste with no name of its own.
    """
    path = (path or "").replace("\\", "/")
    work = session_path(config.get("workspace") or "").rstrip("/")
    machine = path
    here = session_path(anchor).rstrip("/") if anchor else work
    if here and not machine.startswith("/"):
        # The executor's shell keeps its own cwd across commands; a relative
        # path is only unambiguous once it is anchored where the session is.
        machine = here + "/" + machine
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


def chat_send_attachments(client, config, args, invoke, payload):
    """The `attachments` a `chat_send` named, read on the machine as bytes.

    The publication route takes the files with the message (see
    `topics_messages._publish_as_agent`), and the executor's paths mean nothing
    to the backend: each one is read here, through the same `invoke` every
    machine operation uses, and carried as base64 beside the text. The
    workspace-relative spelling is resolved the same way `SendUserFile` resolves
    it, so a path written from the checkout is the path the person meant.
    """
    paths = args.get("attachments") or []
    if not isinstance(paths, list):
        # i18n-exempt: the agent reads this tool refusal, not a person's screen
        raise RuntimeError("attachments must be a list of file paths")
    if len(paths) > MAX_CHAT_SEND_ATTACHMENTS:
        # i18n-exempt: the agent reads this tool refusal, not a person's screen
        raise RuntimeError(
            f"one message carries at most {MAX_CHAT_SEND_ATTACHMENTS} pictures"
        )
    files = []
    for index, path in enumerate(paths):
        if not isinstance(path, str) or not path.strip():
            # i18n-exempt: the agent reads this tool refusal, not a person's screen
            raise RuntimeError("each attachment must be a file path")
        machine, _ = send_user_file_paths(
            path, config, anchor=client.working_directory()
        )
        name = machine.rstrip("/").rsplit("/", 1)[-1] or "image"
        size = stat_file_on_the_machine(
            invoke, machine, f"{payload['id']}-chat-{index}-stat"
        )
        if size > MAX_SEND_USER_FILE_BYTES:
            # i18n-exempt: the agent reads this tool refusal, not a person's screen
            raise RuntimeError(
                f"{path} is larger than "
                f"{MAX_SEND_USER_FILE_BYTES // (1024 * 1024)}MB"
            )
        raw = read_file_on_the_machine(
            invoke, machine, f"{payload['id']}-chat-{index}"
        )
        if not raw:
            # i18n-exempt: the agent reads this tool refusal, not a person's screen
            raise RuntimeError(f"{path} is empty")
        files.append(
            {
                "path": machine,
                "name": name,
                "content_b64": base64.b64encode(raw).decode("ascii"),
            }
        )
    return {**args, "attachments": files}


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
        preexisting = item.get("upload_error")
        # Only a file read from the machine needs its directory asked for, and
        # asking can take the machine's hands (a lease still being prepared).
        # The other two deliver without a machine, and the launch already
        # recorded where the session started: no call, same answer.
        anchor = None
        if not preexisting and not item.get("data_b64"):
            anchor = client.working_directory()
        else:
            anchor = config.get("start_directory")
        machine, rel = send_user_file_paths(path, config, anchor=anchor)
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
