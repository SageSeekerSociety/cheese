"""反馈附件：**读它的判据就是读那条反馈的判据**。

方案稿 §8.7 在附件还不存在的时候就把第一条判据写下来了：私密报告的附件不能被一个
公开地址读到，否则「私密」是假的。所以这里钉的不是接口形状，是四件产品上的事：

* 一条**私密**反馈里的截图，对第三方既读不到也列不出来 —— 404，不是 403。
* 一条**公开**反馈里的截图，登录的人读得到（和它所属的那条反馈同一道门）。
* 附件 id 会经过浏览器，所以它**挂不到别人的报告上**，也挂不到第二条报告上。
* 没有提交就放弃的那些附件，只有上传者本人读得到 —— 那一刻它不属于任何一条报告，
  没有别的话可问。

上限那一条（一份文件多大、最多几份）量的是「报出去的那个数就是拦下来用的那个数」，
所以把配置改小两边一起看，不把 10MB 抄进断言：那条界线在哪里与上限多大无关。
"""

from __future__ import annotations

import asyncio
import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core import blob_store
from app.domain.feedback.repositories import FeedbackRepository
from app.domain.library import service as library
from tests.integration.conftest import session_auth_headers

REPORTER = "att-reporter"
STRANGER = "att-stranger"

#: 一张最小的 PNG：头 12 个字节就够 `raster_images.sniff` 认出它是图片了，而这里量
#: 的是「按不按图片发」，不是能不能解码。
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"x" * 40


def _upload(
    client: TestClient,
    handle: str,
    *,
    content: bytes = PNG_BYTES,
    name: str = "shot.png",
    content_type: str = "image/png",
) -> dict:
    r = client.post(
        "/feedback/attachments",
        files={"file": (name, io.BytesIO(content), content_type)},
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _report_with(client: TestClient, handle: str, attachment_ids: list[str], **body):
    """提一条带附件的反馈，返回详情。"""
    payload = {
        "title": "按钮点了没反应",
        "attachment_ids": attachment_ids,
        **body,
    }
    r = client.post("/feedback", json=payload, headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _read(client: TestClient, attachment_id: str, handle: str | None, **params):
    headers = session_auth_headers(handle) if handle else None
    return client.get(f"/feedback/attachments/{attachment_id}", headers=headers, params=params)


# --- 读：判据就是那条反馈的判据 ------------------------------------------------


def test_a_private_reports_screenshot_is_unreadable_by_a_stranger(client):
    uploaded = _upload(client, REPORTER)
    row = _report_with(client, REPORTER, [uploaded["id"]], visibility="private")

    # 作者自己看得见（他刚提的那条，截图是他传的）。
    assert _read(client, uploaded["id"], REPORTER).status_code == 200

    # 别人拿不到字节 —— 而且和详情一样是 404：403 等于承认这份东西存在。
    assert _read(client, uploaded["id"], STRANGER).status_code == 404
    # 未登录也一样。
    assert _read(client, uploaded["id"], None).status_code == 404

    # 详情里的清单里也不出现（列表不是侧门）。
    detail = client.get(
        f"/feedback/{row['id']}", headers=session_auth_headers(STRANGER)
    )
    assert detail.status_code == 404


def test_a_public_reports_screenshot_is_readable_by_any_signed_in_person(client):
    uploaded = _upload(client, REPORTER)
    _report_with(client, REPORTER, [uploaded["id"]])

    r = _read(client, uploaded["id"], STRANGER)
    assert r.status_code == 200, r.text
    assert r.content == PNG_BYTES


def test_the_bytes_are_served_inert(client):
    """这些字节是人上传的，而页面和它同源、token 在 localStorage 里：一个 HTML 当
    文档跑起来就是拿着读者的会话跑（`app/api/inert_headers.py`）。"""
    uploaded = _upload(
        client, REPORTER, content=b"<script>alert(1)</script>", name="x.html", content_type="text/html"
    )
    _report_with(client, REPORTER, [uploaded["id"]])

    r = _read(client, uploaded["id"], REPORTER)
    assert r.status_code == 200, r.text
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]


def test_a_png_that_is_not_a_png_is_served_as_an_opaque_download(client):
    """浏览器说它是图片是一句声称，字节说了才算：认不出来的不许进 `<img>`。"""
    uploaded = _upload(
        client, REPORTER, content=b"not an image at all", name="fake.png", content_type="image/png"
    )
    _report_with(client, REPORTER, [uploaded["id"]])

    assert uploaded["content_type"] == "application/octet-stream"


def test_download_true_sends_a_filename(client):
    uploaded = _upload(client, REPORTER)
    _report_with(client, REPORTER, [uploaded["id"]])

    r = _read(client, uploaded["id"], REPORTER, download="true")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]


# --- 挂：id 会经过浏览器 --------------------------------------------------------


def test_an_attachment_someone_else_uploaded_cannot_be_filed_on_my_report(client):
    """挂别人的截图是被拒的：那是让一张别人的私密截图公开的便宜办法。"""
    theirs = _upload(client, STRANGER)

    r = client.post(
        "/feedback",
        json={"title": "我的反馈", "attachment_ids": [theirs["id"]]},
        headers=session_auth_headers(REPORTER),
    )
    assert r.status_code == 403, r.text


def test_an_attachment_already_on_one_report_cannot_go_on_a_second(client):
    """一份文件属于一条反馈。第二次挂上去会让那个 id 的可见性说不清。"""
    uploaded = _upload(client, REPORTER)
    _report_with(client, REPORTER, [uploaded["id"]])

    r = client.post(
        "/feedback",
        json={"title": "另一条", "attachment_ids": [uploaded["id"]]},
        headers=session_auth_headers(REPORTER),
    )
    assert r.status_code == 422, r.text


def test_an_unknown_attachment_id_is_refused_rather_than_ignored(client):
    """**不挑能挂的挂上** —— 那会让人以为截图传上去了，而屏幕上少一张。"""
    r = client.post(
        "/feedback",
        json={
            "title": "一条反馈",
            "attachment_ids": ["00000000-0000-0000-0000-000000000000"],
        },
        headers=session_auth_headers(REPORTER),
    )
    assert r.status_code == 404, r.text


def test_a_filed_report_lists_its_attachments_in_the_order_they_were_uploaded(client):
    """上传顺序就是表单上的顺序，也就是报告里的顺序。

    `attachment_ids` 的顺序**不**决定显示顺序：那份数组是浏览器把 id 交回来用的，
    而「这条反馈按什么顺序排它的文件」是行自己的事实（`created_at`）。两份顺序就是
    两个真相，而它们漂开的表现是「表单上第三张、详情页里第一张」。
    """
    first = _upload(client, REPORTER, name="first.png")
    second = _upload(client, REPORTER, name="second.png")

    row = _report_with(client, REPORTER, [second["id"], first["id"]])
    assert [a["name"] for a in row["attachments"]] == ["first.png", "second.png"]


def test_an_uploaded_but_unfiled_attachment_is_readable_only_by_its_uploader(client):
    """表单在选中那一刻就上传，所以「还没挂到任何报告上」是一个真实存在的状态。
    那一刻只有上传者本人读得到：它不属于任何一条报告，没有别的话可问。"""
    loose = _upload(client, REPORTER)

    assert _read(client, loose["id"], REPORTER).status_code == 200
    assert _read(client, loose["id"], STRANGER).status_code == 404


# --- 上限：报出去的那个数就是拦下来用的那个数 ----------------------------------


@pytest.fixture
def small_ceiling(monkeypatch: pytest.MonkeyPatch) -> int:
    ceiling = 4096
    monkeypatch.setattr(library, "MAX_FILE_BYTES", ceiling)
    return ceiling


def test_meta_reports_the_ceiling_the_upload_actually_enforces(client, small_ceiling):
    """报出去的数和拦下来用的数是同一个：把配置改小，两边一起跟着动。"""
    meta = client.get("/feedback/meta").json()["data"]
    assert meta["max_attachment_bytes"] == small_ceiling
    assert meta["max_attachments"] >= 1

    # 正好等于上限的那一份收得下。
    assert _upload(client, REPORTER, content=b"x" * small_ceiling)["bytes"] == small_ceiling

    # 多一个字节就被拒，而且拒的是「太大」，不是别的。
    r = client.post(
        "/feedback/attachments",
        files={
            "file": (
                "big.bin",
                io.BytesIO(b"x" * (small_ceiling + 1)),
                "application/octet-stream",
            )
        },
        headers=session_auth_headers(REPORTER),
    )
    assert r.status_code == 422, r.text


def test_more_attachments_than_the_cap_is_refused_at_submit(client):
    meta = client.get("/feedback/meta").json()["data"]
    ids = [
        _upload(client, REPORTER, name=f"shot-{i}.png")["id"]
        for i in range(meta["max_attachments"] + 1)
    ]

    r = client.post(
        "/feedback",
        json={"title": "太多了", "attachment_ids": ids},
        headers=session_auth_headers(REPORTER),
    )
    # 400，不是 422：这一条是**请求体形状**上的拒绝（Pydantic 的 `max_length`），
    # 由 `validation_exception_handler` 统一答 400。422 留给业务规则（文件太大、
    # 附件已经不是无主的了）。
    assert r.status_code == 400, r.text


# --- 字节进了哪儿 --------------------------------------------------------------


def test_the_bytes_land_in_the_blob_store_not_a_public_url(client):
    """方案稿 §8.7 那条硬要求：它不能有一个公开地址 —— 所以字节落在文件存储里，
    而不是 `/uploads`（那是通用附件那条路，任何人都读得到）。

    接口给回来的这一份**没有 url 字段**：客户端能拿到的只有一个 id，而读那个 id 要
    过 `may_see`。这里读的是**那一行说的位置**，确认字节真的躺在文件存储的键下、
    键是按行 id 走的（不是名字，也不是一个猜得出来的地址）。

    `client.test_factory` 而不是 `db_session`：`client` 读的是它自己那个库，而
    `db_session` 绑的是另一个（`_backdate` 的说明写的就是这件事）。存储那一侧则是在
    这个新循环里直接读文件，不经 app。
    """
    uploaded = _upload(client, REPORTER)
    _report_with(client, REPORTER, [uploaded["id"]])

    assert "url" not in uploaded

    async def _go() -> None:
        async with client.test_factory() as session:
            row = await FeedbackRepository(session).get_attachment(
                uuid.UUID(uploaded["id"])
            )
            assert row is not None
            assert row.location == blob_store.for_new().location
            assert row.blob_key == f".feedback-blobs/{uploaded['id']}"
            assert await blob_store.store(row.location).get(row.blob_key) == PNG_BYTES

    asyncio.run(_go())
