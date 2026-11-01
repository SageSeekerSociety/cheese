"""Putting a file into a library — a project's, or a team's — the way the
library page does.

The page asks for an upload, puts each part at the address it is handed for
that part, and then registers what arrived with the SHA-256 of the whole and the
MD5 of each part. A test that only cares that a file is in the library calls
:func:`put_in` or :func:`replace`; the answer is the first refusal on the way,
or the registration's answer.
"""

import hashlib

import httpx


def _base(project_id: str) -> str:
    return f"/projects/{project_id}/library"


def send_parts(client, base: str, slot: dict, data: bytes, headers=None):
    """Put ``data`` into the store the way the page does, one part at a time
    at the address handed out for it, carrying the headers that came with it.
    The first refusal on the way, or None and the body that registers it."""
    step = slot["part_bytes"]
    digests = []
    for number in range(1, slot["parts"] + 1):
        piece = data[(number - 1) * step : number * step]
        digest = hashlib.md5(piece).hexdigest()
        digests.append(digest)
        address = client.post(
            f"{base}/uploads/part",
            json={"upload": slot["upload"], "part": number, "md5": digest},
            headers=headers,
        )
        if address.status_code != 200:
            return address, None
        where = address.json()["data"]
        put = (
            client.put(where["url"], content=piece, headers=where["headers"])
            if where["url"].startswith("/")
            else httpx.put(where["url"], content=piece, headers=where["headers"])
        )
        if put.status_code != 200:
            return put, None
    return None, {
        "upload": slot["upload"],
        "sha256": hashlib.sha256(data).hexdigest(),
        "parts": digests,
    }


def _send(client, base: str, name: str, data: bytes, headers):
    slot = client.post(
        f"{base}/uploads",
        json={"name": name, "size": len(data)},
        headers=headers,
    )
    if slot.status_code != 200:
        return slot, None
    return send_parts(client, base, slot.json()["data"], data, headers)


def put_in(
    client, project_id: str, name: str, data: bytes, *, folder=None, headers=None
):
    """A new file in the project's library, named ``name`` (in ``folder``)."""
    return put_into(
        client, _base(project_id), name, data, folder=folder, headers=headers
    )


def put_into(client, base: str, name: str, data: bytes, *, folder=None, headers=None):
    """A new file in the library at ``base`` (``/teams/<id>/library``, say)."""
    refused, uploaded = _send(client, base, name, data, headers)
    if refused is not None:
        return refused
    body = {**uploaded, "filename": name}
    if folder is not None:
        body["folder"] = folder
    return client.post(base, json=body, headers=headers)


def replace(client, project_id: str, path: str, data: bytes, *, headers=None):
    """A new version of the project's library file at ``path``."""
    return replace_in(client, _base(project_id), path, data, headers=headers)


def replace_in(client, base: str, path: str, data: bytes, *, headers=None):
    """A new version of the file at ``path`` in the library at ``base``."""
    refused, uploaded = _send(client, base, path.rsplit("/", 1)[-1], data, headers)
    if refused is not None:
        return refused
    return client.put(
        base,
        params={"path": path},
        json=uploaded,
        headers=headers,
    )
