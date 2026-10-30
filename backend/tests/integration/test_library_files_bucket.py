"""资料库的字节可以存进对象存储，而每一份记着自己在哪。

- 新文件写到配置指定的地方；配置改了，已经存下的照旧在原处读得到，什么都不用搬。
- 资料库页上传的字节由浏览器分段直接放进存储，每段带着自己的 SHA-256：没传完整的
  不登记，说的哈希和传上来的对不上的不登记，一个项目领到的上传凭据拿到别的项目去
  登记不认。存进文件桶的文件可以有几 GB，存在本服务器盘上的照旧是原来的上限。
- 机器取一份资料是先要一个地址，再从那里直接取，断了能接着取。
- 读一份资料、拖一段录屏的进度条，不管字节在哪，读到的都是放进去的那些字节。
"""

import hashlib

import httpx
import pytest

from app.core import blob_store
from app.core.config import settings
from tests.conftest import seed_user
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


def _slot(client, project_id: str, name: str, size: int):
    return client.post(
        f"/projects/{project_id}/library/uploads", json={"name": name, "size": size}
    )


def test_an_upload_that_did_not_arrive_whole_is_not_registered(client, new_files_go_to):
    project_id = _project(client)
    new_files_go_to("bucket")
    slot = _slot(client, project_id, "a.md", 10).json()["data"]
    r = client.post(
        f"/projects/{project_id}/library",
        json={
            "upload": slot["upload"],
            "sha256": hashlib.sha256(b"0123456789").hexdigest(),
            "parts": [hashlib.md5(b"0123456789").hexdigest()],
            "filename": "a.md",
        },
    )

    assert r.status_code == 422
    assert _library(client, project_id) == []


def test_an_upload_is_registered_once(client, new_files_go_to):
    project_id = _project(client)
    new_files_go_to("bucket")
    base = f"/projects/{project_id}/library"
    slot = _slot(client, project_id, "a.md", 2).json()["data"]
    _, uploaded = library_uploads.send_parts(client, base, slot, b"hi")

    first = client.post(base, json={**uploaded, "filename": "a.md"})
    again = client.post(base, json={**uploaded, "filename": "b.md"})

    assert first.status_code == 200, first.text
    assert again.status_code == 422
    assert [row["path"] for row in _library(client, project_id)] == ["a.md"]


def test_an_upload_for_one_project_cannot_be_registered_in_another(
    client, new_files_go_to
):
    first = _project(client)
    second = _project(client)
    new_files_go_to("local")
    slot = _slot(client, first, "a.md", 2).json()["data"]
    _, uploaded = library_uploads.send_parts(
        client, f"/projects/{first}/library", slot, b"hi"
    )

    r = client.post(
        f"/projects/{second}/library", json={**uploaded, "filename": "a.md"}
    )

    assert r.status_code == 422
    assert _library(client, second) == []


def test_a_file_registered_with_another_digest_is_refused(client, new_files_go_to):
    """The parts carry the MD5 they were sent with; registering them as other
    bytes is refused, and the file is not in the library."""
    project_id = _project(client)
    new_files_go_to("local")
    base = f"/projects/{project_id}/library"
    slot = _slot(client, project_id, "a.md", 2).json()["data"]
    _, uploaded = library_uploads.send_parts(client, base, slot, b"hi")
    other = hashlib.md5(b"ho").hexdigest()

    r = client.post(base, json={**uploaded, "parts": [other], "filename": "a.md"})

    assert r.status_code == 422
    assert _library(client, project_id) == []


def test_files_kept_on_disk_keep_the_disk_ceiling(client, new_files_go_to):
    project_id = _project(client)
    new_files_go_to("local")

    r = _slot(client, project_id, "大.pdf", 10 * 1024 * 1024 + 1)

    assert r.status_code == 422


def test_a_dataset_of_several_gigabytes_gets_an_upload_into_the_bucket(
    client, new_files_go_to
):
    project_id = _project(client)
    new_files_go_to("bucket")

    taken = _slot(client, project_id, "数据集.tar", 5 * 1024**3)
    over = _slot(
        client, project_id, "太大.tar", settings.files_bucket_file_max_bytes + 1
    )

    assert taken.status_code == 200, taken.text
    slot = taken.json()["data"]
    assert slot["parts"] * slot["part_bytes"] >= 5 * 1024**3
    assert over.status_code == 422


def test_a_teams_library_takes_a_file_as_large_as_the_bucket_takes(
    client, new_files_go_to
):
    """A team's library, as it comes, has room for the largest one file the
    bucket takes: that limit is not a number no library can reach."""
    new_files_go_to("bucket")
    headers = {"Authorization": f"Bearer {seed_user(client, 'alice')}"}
    team = client.post(
        "/teams", json={"name": "数据组", "handle": "data-team"}, headers=headers
    )
    assert team.status_code == 201, team.text
    base = f"/teams/{team.json()['data']['team']['id']}/library"

    taken = client.post(
        f"{base}/uploads",
        json={"name": "数据集.tar", "size": settings.files_bucket_file_max_bytes},
        headers=headers,
    )

    assert taken.status_code == 200, taken.text


def _team_room(client, new_files_go_to, store: str) -> dict:
    new_files_go_to(store)
    headers = {"Authorization": f"Bearer {seed_user(client, 'alice')}"}
    team = client.post(
        "/teams", json={"name": "资料组", "handle": f"room-{store}"}, headers=headers
    )
    assert team.status_code == 201, team.text
    room = client.get(
        f"/teams/{team.json()['data']['team']['id']}/library/room", headers=headers
    )
    assert room.status_code == 200, room.text
    return room.json()["data"]


def test_a_teams_library_kept_in_the_bucket_holds_twenty_gigabytes(
    client, new_files_go_to
):
    """Files kept in the bucket: a team's library holds 20 GB."""
    assert _team_room(client, new_files_go_to, "bucket")["total"] == 20 * 1024**3


def test_a_teams_library_kept_on_this_servers_disk_holds_one_gigabyte(
    client, new_files_go_to
):
    """Files kept on this server's own disk: a team's library holds 1 GB, so
    no team can fill the server's disk with 20."""
    assert _team_room(client, new_files_go_to, "local")["total"] == 1024**3


def test_a_file_in_parts_reads_back_whole(client, new_files_go_to, monkeypatch):
    """A file larger than one part arrives in several and reads back as the
    file. The part size is lowered so a test-sized file takes several."""
    monkeypatch.setattr(blob_store, "PART_BYTES", 5 * 1024 * 1024)
    project_id = _project(client)
    new_files_go_to("bucket")
    data = bytes(range(256)) * (11 * 1024 * 1024 // 256)

    r = library_uploads.put_in(client, project_id, "数据集.bin", data)

    assert r.status_code == 200, r.text
    assert _raw(client, project_id, "数据集.bin").content == data


@pytest.mark.parametrize("store", ["local", "bucket"])
def test_a_machine_fetches_a_file_from_the_address_it_is_handed(
    client, new_files_go_to, store
):
    """`cheese library get` asks for an address and fetches the bytes from it,
    from the bucket itself or from this server's own address, and can carry on
    a cut-off fetch from where it stopped."""
    project_id = _project(client)
    new_files_go_to(store)
    data = bytes(range(256)) * 100
    placed = library_uploads.put_in(client, project_id, "表.csv", data, folder="数据")
    assert placed.status_code == 200, placed.text

    link = client.get(
        f"/projects/{project_id}/library/link", params={"path": "library/数据/表.csv"}
    ).json()["data"]
    credential = client.headers.pop("Authorization")
    try:
        if link["url"].startswith("/"):
            whole = client.get(link["url"])
            rest = client.get(link["url"], headers={"Range": "bytes=1000-"})
        else:
            whole = httpx.get(link["url"])
            rest = httpx.get(link["url"], headers={"Range": "bytes=1000-"})
    finally:
        client.headers["Authorization"] = credential

    assert link["bytes"] == len(data)
    assert whole.content == data
    assert rest.content == data[1000:]


def test_an_upload_to_this_server_needs_no_credential_but_its_address(
    client, new_files_go_to
):
    """The browser sends the bytes with nothing but the address it was given,
    as it does to a presigned bucket address; the address alone lets them in,
    and a forged one does not."""
    project_id = _project(client)
    new_files_go_to("local")
    base = f"/projects/{project_id}/library"
    slot = _slot(client, project_id, "a.md", 2).json()["data"]
    digest = hashlib.md5(b"hi").hexdigest()
    address = client.post(
        f"{base}/uploads/part",
        json={"upload": slot["upload"], "part": 1, "md5": digest},
    ).json()["data"]
    credential = client.headers.pop("Authorization")

    forged = client.put("/files/incoming/v1:forged:token", content=b"hi")
    landed = client.put(address["url"], content=b"hi")

    client.headers["Authorization"] = credential
    assert forged.status_code == 422
    assert landed.status_code == 200
    r = client.post(
        base,
        json={
            "upload": slot["upload"],
            "sha256": hashlib.sha256(b"hi").hexdigest(),
            "parts": [digest],
            "filename": "a.md",
        },
    )
    assert r.status_code == 200, r.text
    assert _raw(client, project_id, "a.md").content == b"hi"
