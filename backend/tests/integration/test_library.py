"""资料库: 给进项目的文件按原名留着，任何房间都引用得到。

上传落在项目一级，不在某个话题的目录里——「上周那份预算表」这句话是在一个从没见过
那份文件的房间里说的，所以它的身份是名字，不是随机串。同名不覆盖：撞了取下一个
`(n)`，两次上传就是两份。

附在一条消息上的那一份是这个房间收到的拷贝；资料库里那一份只读。
"""

from tests.integration.conftest import session_auth_headers


def _project(client) -> str:
    client.headers.update(session_auth_headers("user-1"))
    return client.post("/projects", json={"name": "Demo"}).json()["data"]["id"]


def _topic(client, project_id: str, title: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title, "created_by": "user-1"},
    )
    return r.json()["data"]["id"]


def _upload(client, topic_id: str, name: str, content: bytes) -> dict:
    r = client.post(
        f"/topics/{topic_id}/attachments",
        files={"file": (name, content, "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _library(client, project_id: str) -> list[dict]:
    r = client.get(f"/projects/{project_id}/library")
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def test_upload_keeps_the_name_and_a_collision_takes_the_next_number(client):
    project_id = _project(client)
    topic_id = _topic(client, project_id, "房间一")

    first = _upload(client, topic_id, "预算表.xlsx", b"first")
    second = _upload(client, topic_id, "预算表.xlsx", b"second")

    assert first["library_path"] == "预算表.xlsx"
    assert second["library_path"] == "预算表(2).xlsx"

    names = [f["path"] for f in _library(client, project_id)]
    assert sorted(names) == ["预算表(2).xlsx", "预算表.xlsx"]

    for att, content in ((first, b"first"), (second, b"second")):
        raw = client.get(
            f"/topics/{topic_id}/attachments/raw",
            params={"path": att["path"], "download": "true"},
        )
        assert raw.content == content


def test_another_room_attaches_a_file_it_never_saw(client):
    """症状一：跨话题拿不到之前上传的文件。"""
    project_id = _project(client)
    uploaded_in = _topic(client, project_id, "房间一")
    later = _topic(client, project_id, "房间二")

    _upload(client, uploaded_in, "合同.docx", b"PK\x03\x04contract")

    r = client.post(f"/topics/{later}/attachments", data={"library_path": "合同.docx"})
    assert r.status_code == 200, r.text
    att = r.json()["data"]
    assert att["library_path"] == "合同.docx"
    assert att["bytes"] == len(b"PK\x03\x04contract")

    raw = client.get(
        f"/topics/{later}/attachments/raw",
        params={"path": att["path"], "download": "true"},
    )
    assert raw.status_code == 200
    assert raw.content == b"PK\x03\x04contract"

    # 引用一份已有的文件不会在资料库里多出一项。
    assert [f["path"] for f in _library(client, project_id)] == ["合同.docx"]


def test_a_file_the_library_does_not_have(client):
    project_id = _project(client)
    topic_id = _topic(client, project_id, "房间一")

    missing = client.post(
        f"/topics/{topic_id}/attachments", data={"library_path": "不存在.xlsx"}
    )
    assert missing.status_code == 404

    escape = client.post(
        f"/topics/{topic_id}/attachments", data={"library_path": "../secret.txt"}
    )
    assert escape.status_code == 422

    neither = client.post(f"/topics/{topic_id}/attachments")
    assert neither.status_code == 422


def test_the_library_is_the_project_members_only(client):
    project_id = _project(client)
    topic_id = _topic(client, project_id, "房间一")
    _upload(client, topic_id, "预算表.xlsx", b"first")

    client.headers.update(session_auth_headers("outsider"))
    assert client.get(f"/projects/{project_id}/library").status_code == 403
