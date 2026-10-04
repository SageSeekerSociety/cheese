"""资料库文件的版本：列出每一版、下载旧版、把旧版恢复成现在这一份。

恢复是**复制**：选中的旧版成为新的一版，历史只增不减，被恢复的那一版和它之后的几
版都还在。
"""

from tests.integration.test_library import _library, _project


def _put_in(client, project_id: str, name: str, content: bytes) -> None:
    r = client.post(
        f"/projects/{project_id}/library",
        files={"file": (name, content, "application/octet-stream")},
    )
    assert r.status_code == 200, r.text


def _replace(client, project_id: str, name: str, content: bytes) -> None:
    r = client.put(
        f"/projects/{project_id}/library",
        params={"path": name},
        files={"file": (name, content, "application/octet-stream")},
    )
    assert r.status_code == 200, r.text


def _versions(client, project_id: str, name: str) -> list[dict]:
    r = client.get(f"/projects/{project_id}/library/versions", params={"path": name})
    assert r.status_code == 200, r.text
    return r.json()["data"]["versions"]


def _raw(client, project_id: str, name: str, version: str | None = None) -> bytes:
    params = {"path": name}
    if version:
        params["version"] = version
    r = client.get(f"/projects/{project_id}/library/raw", params=params)
    assert r.status_code == 200, r.text
    return r.content


def test_each_version_is_listed_newest_first_and_downloadable(client):
    project_id = _project(client)
    _put_in(client, project_id, "预算.xlsx", b"one")
    _replace(client, project_id, "预算.xlsx", b"two!")

    versions = _versions(client, project_id, "预算.xlsx")
    assert [v["version"] for v in versions] == [2, 1]
    assert [v["current"] for v in versions] == [True, False]
    assert [v["bytes"] for v in versions] == [4, 3]
    assert versions[0]["added_by"] == "user-1"
    assert _raw(client, project_id, "预算.xlsx", versions[1]["id"]) == b"one"
    assert _raw(client, project_id, "预算.xlsx", versions[0]["id"]) == b"two!"


def test_restoring_copies_the_old_version_as_a_new_one(client):
    project_id = _project(client)
    _put_in(client, project_id, "说明.md", b"v1")
    _replace(client, project_id, "说明.md", b"v2")
    first = _versions(client, project_id, "说明.md")[-1]

    r = client.post(
        f"/projects/{project_id}/library/restore",
        params={"path": "说明.md", "version": first["id"]},
    )
    assert r.status_code == 200, r.text

    versions = _versions(client, project_id, "说明.md")
    assert [v["version"] for v in versions] == [3, 2, 1], "history only grows"
    assert _raw(client, project_id, "说明.md") == b"v1"
    assert _raw(client, project_id, "说明.md", versions[1]["id"]) == b"v2"
    [row] = _library(client, project_id)
    assert row["replaced"] == 2


def test_the_current_version_cannot_be_restored_onto_itself(client):
    project_id = _project(client)
    _put_in(client, project_id, "a.txt", b"x")
    [only] = _versions(client, project_id, "a.txt")
    r = client.post(
        f"/projects/{project_id}/library/restore",
        params={"path": "a.txt", "version": only["id"]},
    )
    assert r.status_code == 422, r.text


def test_a_version_of_another_file_is_not_found(client):
    project_id = _project(client)
    _put_in(client, project_id, "a.txt", b"x")
    _put_in(client, project_id, "b.txt", b"y")
    [b_version] = _versions(client, project_id, "b.txt")
    r = client.get(
        f"/projects/{project_id}/library/raw",
        params={"path": "a.txt", "version": b_version["id"]},
    )
    assert r.status_code == 404, r.text
