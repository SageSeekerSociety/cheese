"""Putting a file into a library — a project's, or a team's — the way the
library page does.

The page asks for an address, puts the bytes there itself, and then registers
what arrived. A test that only cares that a file is in the library calls
:func:`put_in` or :func:`replace`; the answer is the first refusal on the way,
or the registration's answer.
"""

import httpx


def _base(project_id: str) -> str:
    return f"/projects/{project_id}/library"


def _send(client, base: str, name: str, data: bytes, headers):
    slot = client.post(
        f"{base}/uploads",
        json={"name": name, "size": len(data)},
        headers=headers,
    )
    if slot.status_code != 200:
        return slot, None
    url = slot.json()["data"]["url"]
    put = (
        client.put(url, content=data)
        if url.startswith("/")
        else httpx.put(url, content=data)
    )
    if put.status_code != 200:
        return put, None
    return None, slot.json()["data"]["upload"]


def put_in(
    client, project_id: str, name: str, data: bytes, *, folder=None, headers=None
):
    """A new file in the project's library, named ``name`` (in ``folder``)."""
    return put_into(
        client, _base(project_id), name, data, folder=folder, headers=headers
    )


def put_into(client, base: str, name: str, data: bytes, *, folder=None, headers=None):
    """A new file in the library at ``base`` (``/teams/<id>/library``, say)."""
    refused, upload = _send(client, base, name, data, headers)
    if refused is not None:
        return refused
    body = {"upload": upload, "filename": name}
    if folder is not None:
        body["folder"] = folder
    return client.post(base, json=body, headers=headers)


def replace(client, project_id: str, path: str, data: bytes, *, headers=None):
    """A new version of the project's library file at ``path``."""
    return replace_in(client, _base(project_id), path, data, headers=headers)


def replace_in(client, base: str, path: str, data: bytes, *, headers=None):
    """A new version of the file at ``path`` in the library at ``base``."""
    refused, upload = _send(client, base, path.rsplit("/", 1)[-1], data, headers)
    if refused is not None:
        return refused
    return client.put(
        base,
        params={"path": path},
        json={"upload": upload},
        headers=headers,
    )
