"""资料库一页一页地给：每一层文件夹在前、文件在后，平铺地找也一页一页。

文档在文档表里自己翻页。两串用同一种位置（`rank`），按位置从小到大并起来就是资料库
页那一张混排的表——所以两边各自翻到底、并起来，得到的是和一次全排好一样的顺序。
"""

from tests.integration.conftest import post_project, session_auth_headers


def _project(client) -> str:
    client.headers.update(session_auth_headers("user-1"))
    return post_project(client, json={"name": "Demo"}).json()["data"]["id"]


def _put_in(client, project_id: str, name: str, folder: str | None = None) -> None:
    r = client.post(
        f"/projects/{project_id}/library",
        files={"file": (name, name.encode(), "application/octet-stream")},
        data={"folder": folder} if folder else None,
    )
    assert r.status_code == 200, r.text


def _pages(client, url: str, **params) -> list[list[dict]]:
    """Every page of ``url``, following ``next`` to the end."""
    pages, cursor = [], None
    while True:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        r = client.get(url, params=query)
        assert r.status_code == 200, r.text
        body = r.json()["data"]
        pages.append(body["data"])
        cursor = body["next"]
        if cursor is None:
            return pages


def _label(row: dict) -> str:
    if "type" not in row:
        return f"doc:{row['title']}"
    return f"{row['type']}:{row['path']}"


def test_a_level_comes_folders_first_then_newest_files_page_by_page(client):
    project_id = _project(client)
    for folder in ("甲", "乙", "丙"):
        _put_in(client, project_id, "x.md", folder)
    for name in ("1.md", "2.md", "3.md", "4.md"):
        _put_in(client, project_id, name)

    pages = _pages(client, f"/projects/{project_id}/library", limit=2)
    rows = [_label(row) for page in pages for row in page]
    assert rows == [
        "folder:丙",
        "folder:乙",
        "folder:甲",
        "file:4.md",
        "file:3.md",
        "file:2.md",
        "file:1.md",
    ]
    assert [len(page) for page in pages] == [2, 2, 2, 1]
    whole = _pages(client, f"/projects/{project_id}/library", limit=200)
    assert [_label(row) for row in whole[0]] == rows

    inside = _pages(client, f"/projects/{project_id}/library", dir="甲")
    assert [_label(row) for page in inside for row in page] == ["file:甲/x.md"]


def test_a_file_put_in_while_paging_is_neither_repeated_nor_makes_one_go_missing(
    client,
):
    project_id = _project(client)
    for name in ("1.md", "2.md", "3.md", "4.md"):
        _put_in(client, project_id, name)
    url = f"/projects/{project_id}/library"
    first = client.get(url, params={"limit": 2}).json()["data"]

    _put_in(client, project_id, "新.md")
    rest = _pages(client, url, limit=2, cursor=first["next"])

    seen = [row["path"] for row in first["data"]] + [
        row["path"] for page in rest for row in page
    ]
    assert seen == ["4.md", "3.md", "2.md", "1.md"]


def test_searching_and_filtering_cover_the_whole_library(client):
    project_id = _project(client)
    _put_in(client, project_id, "报价.xlsx", "合同/2026")
    _put_in(client, project_id, "报价说明.md", "合同")
    _put_in(client, project_id, "照片.png")
    _put_in(client, project_id, "README")

    url = f"/projects/{project_id}/library"
    found = _pages(client, url, q="报价", limit=1)
    assert [row["path"] for page in found for row in page] == [
        "合同/报价说明.md",
        "合同/2026/报价.xlsx",
    ]
    sheets = _pages(client, url, kind="sheet")
    assert [row["path"] for page in sheets for row in page] == ["合同/2026/报价.xlsx"]
    everything = _pages(client, url, flat="true", limit=3)
    assert [row["path"] for page in everything for row in page] == [
        "README",
        "照片.png",
        "合同/报价说明.md",
        "合同/2026/报价.xlsx",
    ]
    other = _pages(client, url, kind="other")
    assert [row["path"] for page in other for row in page] == ["README"]
    assert client.get(url, params={"kind": "video"}).status_code == 422


def test_library_and_documents_merge_by_rank_into_one_order(client):
    project_id = _project(client)
    created = []
    for i in range(3):
        _put_in(client, project_id, f"文件{i}.md")
        created.append(f"file:文件{i}.md")
        r = client.post(f"/projects/{project_id}/documents", json={"title": f"文档{i}"})
        assert r.status_code == 200, r.text
        created.append(f"doc:文档{i}")
    _put_in(client, project_id, "x.md", "夹")

    library = _pages(client, f"/projects/{project_id}/library", limit=2)
    documents = _pages(client, f"/projects/{project_id}/documents", limit=2)
    rows = [row for page in library + documents for row in page]
    merged = [_label(row) for row in sorted(rows, key=lambda row: row["rank"])]
    assert merged == ["folder:夹", *reversed(created)]


def test_a_named_file_and_the_folder_list_are_there_without_paging(client):
    project_id = _project(client)
    _put_in(client, project_id, "报价.xlsx", "合同/2026")
    _put_in(client, project_id, "x.md", "归档")

    r = client.get(
        f"/projects/{project_id}/library/file", params={"path": "合同/2026/报价.xlsx"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["path"] == "合同/2026/报价.xlsx"
    assert r.json()["data"]["added_by"] == "user-1"
    assert (
        client.get(
            f"/projects/{project_id}/library/file", params={"path": "没有.md"}
        ).status_code
        == 404
    )

    folders = client.get(f"/projects/{project_id}/library/folders").json()["data"]
    assert folders["folders"] == ["合同", "合同/2026", "归档"]
