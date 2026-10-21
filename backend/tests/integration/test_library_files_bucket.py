"""资料库的字节可以存进对象存储，而每一份记着自己在哪。

- 新文件写到配置指定的地方；配置改了，已经存下的照旧在原处读得到，什么都不用搬。
- 资料库页上传的字节由浏览器直接放进存储，后端登记时自己量大小、算哈希：没传
  完整的不登记，一个项目领到的上传凭据拿到别的项目去登记不认。
- 读一份资料、拖一段录屏的进度条，不管字节在哪，读到的都是放进去的那些字节。
"""

import pytest

from app.core.config import settings
from tests.integration.test_library import _library, _project
from tests.support import library_uploads
from tests.support.files_bucket import files_bucket


@pytest.fixture(scope="module")
def bucket_config():
    with files_bucket() as config:
        yield config


@pytest.fixture
def new_files_go_to(monkeypatch, bucket_config):
    for name, value in bucket_config.items():
        monkeypatch.setattr(settings, name, value)

    def go_to(store: str) -> None:
        monkeypatch.setattr(settings, "files_store", store)

    return go_to


def _raw(client, project_id: str, name: str, **headers):
    return client.get(
        f"/projects/{project_id}/library/raw", params={"path": name}, headers=headers
    )


def test_a_file_put_in_the_bucket_reads_back_after_new_files_go_local_again(
    client, new_files_go_to
):
    project_id = _project(client)
    new_files_go_to("bucket")
    assert (
        library_uploads.put_in(
            client, project_id, "报告.md", b"in the bucket"
        ).status_code
        == 200
    )

    new_files_go_to("local")
    assert (
        library_uploads.put_in(client, project_id, "笔记.md", b"on disk").status_code
        == 200
    )

    assert _raw(client, project_id, "报告.md").content == b"in the bucket"
    assert _raw(client, project_id, "笔记.md").content == b"on disk"
    assert sorted(row["path"] for row in _library(client, project_id)) == [
        "报告.md",
        "笔记.md",
    ]


def test_replacing_a_local_file_keeps_its_old_version_readable(client, new_files_go_to):
    project_id = _project(client)
    new_files_go_to("local")
    library_uploads.put_in(client, project_id, "说明.md", b"v1")
    new_files_go_to("bucket")
    assert (
        library_uploads.replace(client, project_id, "说明.md", b"v2").status_code == 200
    )

    versions = client.get(
        f"/projects/{project_id}/library/versions", params={"path": "说明.md"}
    ).json()["data"]["versions"]
    old = next(v for v in versions if not v["current"])
    first = client.get(
        f"/projects/{project_id}/library/raw",
        params={"path": "说明.md", "version": old["id"]},
    )
    assert first.content == b"v1"
    assert _raw(client, project_id, "说明.md").content == b"v2"


def test_a_stretch_of_a_file_in_the_bucket_is_that_stretch(client, new_files_go_to):
    project_id = _project(client)
    new_files_go_to("bucket")
    data = bytes(range(256)) * 8000
    library_uploads.put_in(client, project_id, "录屏.mp4", data)

    part = _raw(client, project_id, "录屏.mp4", Range="bytes=1000-1999")

    assert part.status_code == 206
    assert part.content == data[1000:2000]
    assert part.headers["content-range"] == f"bytes 1000-1999/{len(data)}"


def test_an_upload_that_did_not_arrive_whole_is_not_registered(client, new_files_go_to):
    project_id = _project(client)
    new_files_go_to("bucket")
    slot = client.post(
        f"/projects/{project_id}/library/uploads", json={"name": "a.md", "size": 10}
    ).json()["data"]

    r = client.post(
        f"/projects/{project_id}/library",
        json={"upload": slot["upload"], "filename": "a.md"},
    )

    assert r.status_code == 422
    assert _library(client, project_id) == []


def test_an_upload_for_one_project_cannot_be_registered_in_another(
    client, new_files_go_to
):
    first = _project(client)
    second = _project(client)
    new_files_go_to("local")
    slot = client.post(
        f"/projects/{first}/library/uploads", json={"name": "a.md", "size": 2}
    ).json()["data"]
    assert client.put(slot["url"], content=b"hi").status_code == 200

    r = client.post(
        f"/projects/{second}/library",
        json={"upload": slot["upload"], "filename": "a.md"},
    )

    assert r.status_code == 422
    assert _library(client, second) == []


def test_a_file_over_the_ceiling_gets_no_upload_address(client, new_files_go_to):
    project_id = _project(client)
    new_files_go_to("bucket")

    r = client.post(
        f"/projects/{project_id}/library/uploads",
        json={"name": "大.pdf", "size": 10 * 1024 * 1024 + 1},
    )

    assert r.status_code == 422
