"""资料库的文件夹：名字里的 `/` 就是文件夹。

挪动和改名只改名字：字节、被替换下来的几版、引用它的消息都跟着这一份走——消息里的
chip 指的是这一份资料，它换了个名字，还是它。
"""

import asyncio
import uuid

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.library import records as library_records
from tests.integration.conftest import post_project, session_auth_headers


def _project(client) -> str:
    client.headers.update(session_auth_headers("user-1"))
    return post_project(client, json={"name": "Demo"}).json()["data"]["id"]


def _topic(client, project_id: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": "房间一"})
    return r.json()["data"]["id"]


def _put_in(client, project_id: str, name: str, data: bytes, folder=None) -> str:
    r = client.post(
        f"/projects/{project_id}/library",
        files={"file": (name, data, "application/octet-stream")},
        data={"folder": folder} if folder is not None else None,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["path"]


def _level(client, project_id: str, **params) -> tuple[list[dict], str | None]:
    r = client.get(f"/projects/{project_id}/library", params=params)
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"], r.json()["data"]["next"]


def _names(client, project_id: str, dir: str = "") -> list[str]:
    """Every file under ``dir``, by walking its folders one level at a time."""
    rows, _ = _level(client, project_id, dir=dir, limit=200)
    names: list[str] = []
    for row in rows:
        if row["type"] == "folder":
            names += _names(client, project_id, row["path"])
        else:
            names.append(row["path"])
    return sorted(names)


def _raw(client, project_id: str, name: str):
    return client.get(f"/projects/{project_id}/library/raw", params={"path": name})


def _move(client, project_id: str, path: str, to: str):
    return client.post(
        f"/projects/{project_id}/library/move", json={"path": path, "to": to}
    )


def _said(client, project_id: str, topic_id: str, kind: BlockKind, content: str):
    """A message already in the room that names a library file."""

    async def say() -> uuid.UUID:
        async with client.test_factory() as session:
            block = Block(
                project_id=uuid.UUID(project_id),
                conversation_id=uuid.UUID(topic_id),
                kind=kind,
                author_type=AuthorType.participant,
                author="user-1",
                content=content,
            )
            session.add(block)
            await session.commit()
            return block.id

    return asyncio.run(say())


def _content(client, block_id: uuid.UUID) -> str:
    async def read() -> str:
        async with client.test_factory() as session:
            block = await session.get(Block, block_id)
            assert block is not None
            return block.content

    return asyncio.run(read())


def test_a_file_put_into_a_folder_is_named_by_it(client):
    project_id = _project(client)

    assert _put_in(client, project_id, "报价.xlsx", b"q", folder="合同/2026") == (
        "合同/2026/报价.xlsx"
    )
    assert _put_in(client, project_id, "报价.xlsx", b"q2", folder="合同/2026") == (
        "合同/2026/报价(2).xlsx"
    )
    assert _names(client, project_id) == [
        "合同/2026/报价(2).xlsx",
        "合同/2026/报价.xlsx",
    ]
    assert _raw(client, project_id, "合同/2026/报价.xlsx").content == b"q"


def test_a_folder_name_that_is_not_a_name_is_refused(client):
    project_id = _project(client)
    for folder in ("../外面", "合同/.隐藏", "a//b"):
        r = client.post(
            f"/projects/{project_id}/library",
            files={"file": ("x.txt", b"x", "text/plain")},
            data={"folder": folder},
        )
        assert r.status_code == 422, folder
    assert _names(client, project_id) == []


def test_a_name_is_never_both_a_file_and_a_folder(client):
    project_id = _project(client)
    _put_in(client, project_id, "附录.md", b"a", folder="报告")

    # 「报告」已经是一个文件夹：同名的文件拿下一个号，不和文件夹混成一个名字。
    assert _put_in(client, project_id, "报告", b"file") == "报告(2)"
    # 反过来，往一份文件「下面」挪东西也不行。
    assert (
        _move(client, project_id, "报告/附录.md", "报告(2)/附录.md").status_code == 422
    )


def test_moving_a_file_takes_its_bytes_versions_and_references_along(client):
    project_id = _project(client)
    topic_id = _topic(client, project_id)
    _put_in(client, project_id, "说明.md", b"v1")
    replaced = client.put(
        f"/projects/{project_id}/library",
        params={"path": "说明.md"},
        files={"file": ("说明.md", b"v2", "text/plain")},
    )
    assert replaced.status_code == 200, replaced.text
    attachment = _said(
        client, project_id, topic_id, BlockKind.attachment, "library/说明.md"
    )
    message = _said(
        client,
        project_id,
        topic_id,
        BlockKind.message,
        "看一下 <&library/说明.md> 和 <&library/说明.md.bak>",
    )

    moved = _move(client, project_id, "说明.md", "归档/说明.md")
    assert moved.status_code == 200, moved.text

    assert _names(client, project_id) == ["归档/说明.md"]
    assert _raw(client, project_id, "归档/说明.md").content == b"v2"
    assert _raw(client, project_id, "说明.md").status_code == 404
    versions = client.get(
        f"/projects/{project_id}/library/versions", params={"path": "归档/说明.md"}
    ).json()["data"]["versions"]
    assert [v["bytes"] for v in versions] == [2, 2]
    old = next(v for v in versions if not v["current"])
    assert (
        client.get(
            f"/projects/{project_id}/library/raw",
            params={"path": "归档/说明.md", "version": old["id"]},
        ).content
        == b"v1"
    )

    # 旧消息里的引用跟着改名；只是名字像的别的文件不动。
    assert _content(client, attachment) == "library/归档/说明.md"
    assert _content(client, message) == (
        "看一下 <&library/归档/说明.md> 和 <&library/说明.md.bak>"
    )
    opened = client.get(
        f"/topics/{topic_id}/attachments/raw",
        params={"path": "library/归档/说明.md", "download": "true"},
    )
    assert opened.content == b"v2"


def test_renaming_a_folder_moves_everything_in_it(client):
    project_id = _project(client)
    topic_id = _topic(client, project_id)
    _put_in(client, project_id, "a.md", b"a", folder="调研")
    _put_in(client, project_id, "b.md", b"b", folder="调研/前端")
    _put_in(client, project_id, "c.md", b"c", folder="调研二")
    message = _said(
        client, project_id, topic_id, BlockKind.message, "<&library/调研/前端/b.md>"
    )

    assert _move(client, project_id, "调研", "2026/调研").status_code == 200

    assert _names(client, project_id) == [
        "2026/调研/a.md",
        "2026/调研/前端/b.md",
        "调研二/c.md",
    ]
    assert _raw(client, project_id, "2026/调研/前端/b.md").content == b"b"
    assert _content(client, message) == "<&library/2026/调研/前端/b.md>"


def test_a_move_onto_a_taken_name_changes_nothing(client):
    project_id = _project(client)
    _put_in(client, project_id, "a.md", b"a", folder="甲")
    _put_in(client, project_id, "a.md", b"other", folder="乙")

    refused = _move(client, project_id, "甲", "乙")
    assert refused.status_code == 422
    assert _move(client, project_id, "甲", "甲/子").status_code == 422
    assert _move(client, project_id, "没有这个", "丙").status_code == 404
    assert _names(client, project_id) == ["乙/a.md", "甲/a.md"]
    assert _raw(client, project_id, "乙/a.md").content == b"other"


def test_deleting_a_folder_deletes_what_is_in_it(client):
    project_id = _project(client)
    _put_in(client, project_id, "a.md", b"a", folder="草稿")
    _put_in(client, project_id, "b.md", b"b", folder="草稿/旧")
    _put_in(client, project_id, "c.md", b"c", folder="草稿二")

    gone = client.delete(f"/projects/{project_id}/library", params={"path": "草稿"})
    assert gone.status_code == 200, gone.text
    assert _names(client, project_id) == ["草稿二/c.md"]
    assert _raw(client, project_id, "草稿/a.md").status_code == 404


def test_same_name_at_the_same_time_loses_neither(client):
    """两份同名的上传同时在路上：两份都留着，各拿一个名字。"""
    project_id = uuid.UUID(_project(client))
    contents = [f"payload-{i}".encode() for i in range(8)]

    async def put(data: bytes) -> str:
        async with client.test_factory() as session:
            name = await library_records.add(
                session, project_id, "同名.txt", data, "user-1", None
            )
            await session.commit()
            return name

    async def together() -> list[str]:
        return list(await asyncio.gather(*(put(data) for data in contents)))

    names = asyncio.run(together())
    assert len(set(names)) == len(contents)
    assert sorted(_names(client, str(project_id))) == sorted(names)
    assert sorted(
        _raw(client, str(project_id), name).content for name in names
    ) == sorted(contents)
