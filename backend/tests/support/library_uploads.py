"""Putting a file into a project's library the way the library page does.

The page asks for an address, puts the bytes there itself, and then registers
what arrived. A test that only cares that a file is in the library calls
:func:`put_in` or :func:`replace`; the answer is the first refusal on the way,
or the registration's answer.
"""

import httpx


def _send(client, project_id: str, name: str, data: bytes, headers):
    slot = client.post(
        f"/projects/{project_id}/library/uploads",
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
    """A new file in the library, named ``name`` (in ``folder``)."""
    refused, upload = _send(client, project_id, name, data, headers)
    if refused is not None:
        return refused
    body = {"upload": upload, "filename": name}
    if folder is not None:
        body["folder"] = folder
    return client.post(f"/projects/{project_id}/library", json=body, headers=headers)


def replace(client, project_id: str, path: str, data: bytes, *, headers=None):
    """A new version of the library file at ``path``."""
    refused, upload = _send(client, project_id, path.rsplit("/", 1)[-1], data, headers)
    if refused is not None:
        return refused
    return client.put(
        f"/projects/{project_id}/library",
        params={"path": path},
        json={"upload": upload},
        headers=headers,
    )
