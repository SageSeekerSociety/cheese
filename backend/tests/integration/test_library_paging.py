"""资料库一页一页地给：每一层文件夹在前、文件在后，平铺地找也一页一页。

文档在文档表里自己翻页。两串用同一种位置（`rank`），按位置从小到大并起来就是资料库
页那一张混排的表——所以两边各自翻到底、并起来，得到的是和一次全排好一样的顺序。
"""

from tests.integration.conftest import post_project, session_auth_headers
from tests.support import library_uploads


def _project(client) -> str:
    client.headers.update(session_auth_headers("user-1"))
    return post_project(client, json={"name": "Demo"}).json()["data"]["id"]


def _put_in(client, project_id: str, name: str, folder: str | None = None) -> None:
    r = library_uploads.put_in(client, project_id, name, name.encode(), folder=folder)
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


def test_off_is_the_order_it_always_was(client):
    """`sort=off`（也就是不给）和从前一模一样：文件夹一拨，其余一拨。"""
    project_id = _project(client)
    url = f"/projects/{project_id}/library"
    _put_in(client, project_id, "老板.md")
    _put_in(client, project_id, "x.md", "夹")
    _put_in(client, project_id, "最新的.md")

    default = [row["path"] for page in _pages(client, url) for row in page]
    off = [row["path"] for page in _pages(client, url, sort="off") for row in page]
    assert off == default == ["夹", "最新的.md", "老板.md"]


def test_ascending_puts_folders_on_the_same_timeline(client):
    """升序里文件夹不再单独一拨：谁的最近一份更早谁就靠前。"""
    project_id = _project(client)
    url = f"/projects/{project_id}/library"
    _put_in(client, project_id, "一.md")
    _put_in(client, project_id, "二.md", "夹")  # 夹 里最近一份，比 一.md 新
    _put_in(client, project_id, "三.md")

    rows = [row for page in _pages(client, url, sort="asc") for row in page]
    labels = [_label(row) for row in rows]
    # 这一层只列这一层的东西：`夹/二.md` 在文件夹里，这里只算「夹」那一行。
    # 一.md 最早，然后是它放进来的 夹，最后是最新的 三.md。
    assert labels == ["file:一.md", "folder:夹", "file:三.md"]
    # 位置单调递增，正是这条时间线
    assert [row["rank"] for row in rows] == sorted(row["rank"] for row in rows)

    # 比起「关」那一档：文件夹不再被整拨提到最前
    off = [_label(row) for page in _pages(client, url, sort="off") for row in page]
    assert off == ["folder:夹", "file:三.md", "file:一.md"]


def test_descending_is_the_same_timeline_the_other_way(client):
    project_id = _project(client)
    url = f"/projects/{project_id}/library"
    _put_in(client, project_id, "一.md")
    _put_in(client, project_id, "二.md", "夹")
    _put_in(client, project_id, "三.md")

    ascending = [
        _label(row) for page in _pages(client, url, sort="asc") for row in page
    ]
    descending = [
        _label(row) for page in _pages(client, url, sort="desc") for row in page
    ]
    assert descending == list(reversed(ascending))


def test_a_bad_sort_is_refused(client):
    project_id = _project(client)
    r = client.get(f"/projects/{project_id}/library", params={"sort": "size"})
    assert r.status_code == 422


def test_the_timeline_pages_without_repeating_or_losing_a_row(client):
    """一页一页地翻升降，和不翻页一次取完得到的顺序一样。"""
    project_id = _project(client)
    url = f"/projects/{project_id}/library"
    for name in ("a.md", "b.md", "c.md", "d.md"):
        _put_in(client, project_id, name)
    _put_in(client, project_id, "x.md", "夹")

    for sort in ("asc", "desc"):
        paged = [
            row["path"]
            for page in _pages(client, url, sort=sort, limit=2)
            for row in page
        ]
        whole = [
            row["path"]
            for page in _pages(client, url, sort=sort, limit=200)
            for row in page
        ]
        assert paged == whole


def test_documents_join_the_same_timeline(client):
    """文档和文件、文件夹并进同一条线：两串按 rank 并起来就是一次排好的顺序。"""
    project_id = _project(client)
    _put_in(client, project_id, "a.md")
    r = client.post(f"/projects/{project_id}/documents", json={"title": "文档甲"})
    assert r.status_code == 200, r.text
    _put_in(client, project_id, "b.md", "夹")

    for sort in ("asc", "desc"):
        library = [
            row
            for page in _pages(
                client, f"/projects/{project_id}/library", sort=sort, limit=1
            )
            for row in page
        ]
        documents = [
            row
            for page in _pages(
                client, f"/projects/{project_id}/documents", sort=sort, limit=1
            )
            for row in page
        ]
        merged = [
            _label(row) for row in sorted(library + documents, key=lambda r: r["rank"])
        ]
        whole = [
            _label(row)
            for page in _pages(
                client, f"/projects/{project_id}/library", sort=sort, limit=200
            )
            for row in page
        ]
        # 资料库这一串里没有文档；并起来之后文档落在它按时间该在的位置上
        assert "doc:文档甲" in merged
        assert [label for label in merged if label != "doc:文档甲"] == whole


def test_code_files_have_a_kind_of_their_own(client):
    """「代码」一栏：脚本和笔记本不再落在「其他」里。"""
    project_id = _project(client)
    _put_in(client, project_id, "统计.py", "分析")
    _put_in(client, project_id, "探索.ipynb", "分析")
    _put_in(client, project_id, "README")

    url = f"/projects/{project_id}/library"
    code = _pages(client, url, kind="code")
    assert sorted(row["path"] for page in code for row in page) == [
        "分析/探索.ipynb",
        "分析/统计.py",
    ]
    other = _pages(client, url, kind="other")
    assert [row["path"] for page in other for row in page] == ["README"]
