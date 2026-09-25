"""A mailbox lent to a project: its teammates read and draft, only the owner
sends, and what is sent is exactly the draft the owner read.

The IMAP/SMTP layer is replaced by a recording stand-in here; the protocol
itself is exercised against a real mail server in `test_mail_server.py`.
"""

import json
import uuid

import httpx
import pytest

from app.core.config import settings
from app.domain.integration import feishu, mail
from app.domain.integration.mail import IntegrationError
from app.domain.library import service as library
from tests.conftest import seed_user
from tests.integration.conftest import post_project

OWNER = "user-1"


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    # The stand-in mailbox has made-up host names; the guard has its own test.
    monkeypatch.setattr(settings, "integration_allow_private_hosts", True)


class Mailbox:
    """What the stand-in mailbox was asked to do."""

    def __init__(self, monkeypatch):
        self.drafts, self.sent, self.removed = [], [], []
        self.fail_send: Exception | None = None
        monkeypatch.setattr(mail, "check", lambda s: None)
        monkeypatch.setattr(
            mail,
            "search",
            lambda s, **q: [{"uid": "7", "subject": "预算", "from": "bob@x.test"}],
        )
        monkeypatch.setattr(mail, "append_draft", self._draft)
        monkeypatch.setattr(mail, "send", self._send)
        monkeypatch.setattr(mail, "save_sent", lambda s, m: "Sent")
        monkeypatch.setattr(
            mail,
            "remove_by_message_id",
            lambda s, f, mid: self.removed.append(mid) or True,
        )

    def _draft(self, settings_, message):
        self.drafts.append(message)
        return "Drafts"

    def _send(self, settings_, message):
        if self.fail_send:
            raise self.fail_send
        self.sent.append(message)
        return []


@pytest.fixture
def mailbox(monkeypatch):
    return Mailbox(monkeypatch)


def _setup(client):
    person = {"Authorization": f"Bearer {seed_user(client, OWNER)}"}
    r = post_project(
        client, json={"name": f"连接-{uuid.uuid4().hex[:6]}", "owner_handle": OWNER}
    )
    project = r.json()["data"]["id"]
    room = client.post(
        "/topics",
        json={"project_id": project, "title": "对外沟通", "created_by": OWNER},
    ).json()["data"]["id"]
    return person, project, room


def _connect(client, person, grants):
    r = client.post(
        "/me/integrations/mail",
        json={
            "imap_host": "imap.x.test",
            "smtp_host": "smtp.x.test",
            "username": "alice@x.test",
            "password": "app-password-1",
            "grants": grants,
        },
        headers=person,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_the_secret_never_comes_back_and_a_teammate_cannot_manage_it(client, mailbox):
    person, project, _room = _setup(client)
    row = _connect(client, person, [project])
    assert "app-password-1" not in str(row)
    listed = client.get("/me/integrations", headers=person).json()["data"]["data"]
    assert "app-password-1" not in str(listed)
    assert client.get("/me/integrations").status_code in (401, 403)


def test_a_project_the_owner_did_not_name_cannot_use_it(client, mailbox):
    person, project, room = _setup(client)
    row = _connect(client, person, [])
    r = client.post(
        f"/integrations/{row['id']}/mail/search?topic={room}", json={"query": "预算"}
    )
    assert r.status_code == 403, r.text

    client.patch(
        f"/me/integrations/{row['id']}", json={"grants": [project]}, headers=person
    )
    r = client.post(
        f"/integrations/{row['id']}/mail/search?topic={room}", json={"query": "预算"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["messages"][0]["uid"] == "7"


def _draft(client, row, room, attachments=()):
    return client.post(
        f"/integrations/{row['id']}/mail/drafts?topic={room}",
        json={
            "to": ["bob@x.test"],
            "subject": "第三季度预算",
            "body": "附件是核对后的预算表。",
            "attachments": list(attachments),
        },
    )


def test_a_draft_waits_for_the_owner_and_what_is_sent_is_what_was_drafted(
    client, mailbox
):
    person, project, room = _setup(client)
    row = _connect(client, person, [project])
    library.write_room_file(
        uuid.UUID(project), uuid.UUID(room), "out/预算表.xlsx", b"version-1"
    )

    drafted = _draft(client, row, room, ["out/预算表.xlsx"])
    assert drafted.status_code == 200, drafted.text
    draft = drafted.json()["data"]
    assert draft["status"] == "drafted"
    assert len(mailbox.drafts) == 1 and mailbox.sent == []

    assert client.post(f"/me/mail-drafts/{draft['id']}/send").status_code in (401, 403)
    assert mailbox.sent == [], "a teammate sent mail without its owner"

    pending = client.get("/me/mail-drafts", headers=person).json()["data"]["data"]
    assert [d["subject"] for d in pending] == ["第三季度预算"]
    inbox = client.get(f"/projects/{project}/alerts", headers=person).json()["data"][
        "data"
    ]
    assert any("邮件草稿待你确认" in n["title"] for n in inbox)

    sent = client.post(f"/me/mail-drafts/{draft['id']}/send", headers=person)
    assert sent.status_code == 200, sent.text
    assert sent.json()["data"]["draft"]["status"] == "sent"
    [message] = mailbox.sent
    assert message["To"] == "bob@x.test"
    assert message["Subject"] == "第三季度预算"
    assert message["Message-ID"] == mailbox.drafts[0]["Message-ID"]
    [part] = list(message.iter_attachments())
    assert part.get_payload(decode=True) == b"version-1"
    assert mailbox.removed == [message["Message-ID"]]

    again = client.post(f"/me/mail-drafts/{draft['id']}/send", headers=person)
    assert again.status_code >= 400 and len(mailbox.sent) == 1


def test_an_attachment_changed_after_drafting_is_not_sent(client, mailbox):
    person, project, room = _setup(client)
    row = _connect(client, person, [project])
    library.write_room_file(uuid.UUID(project), uuid.UUID(room), "报告.docx", b"v1")
    draft = _draft(client, row, room, ["报告.docx"]).json()["data"]
    library.write_room_file(uuid.UUID(project), uuid.UUID(room), "报告.docx", b"v2")

    r = client.post(f"/me/mail-drafts/{draft['id']}/send", headers=person)

    assert r.status_code >= 400 and "被改过" in r.text
    assert mailbox.sent == []


def test_a_failed_send_is_reported_as_failed(client, mailbox):
    person, project, room = _setup(client)
    row = _connect(client, person, [project])
    draft = _draft(client, row, room).json()["data"]
    mailbox.fail_send = IntegrationError("error", "发送失败：服务器断开")

    r = client.post(f"/me/mail-drafts/{draft['id']}/send", headers=person)

    assert r.status_code >= 400
    [row_after] = client.get("/me/mail-drafts?status=failed", headers=person).json()[
        "data"
    ]["data"]
    assert row_after["status"] == "failed" and "服务器断开" in row_after["error"]


def test_an_expired_password_says_so(client, mailbox, monkeypatch):
    person, project, room = _setup(client)
    row = _connect(client, person, [project])

    def refuse(settings_, **query):
        raise IntegrationError("auth_failed", "邮箱拒绝了登录：密码或授权码可能已失效")

    monkeypatch.setattr(mail, "search", refuse)
    r = client.post(
        f"/integrations/{row['id']}/mail/search?topic={room}", json={"query": "x"}
    )
    assert r.status_code == 401
    assert r.json()["error"]["data"]["kind"] == "auth_failed"
    listed = client.get("/me/integrations", headers=person).json()["data"]["data"]
    assert listed[0]["status"] == "auth_failed"


# ── Feishu, against a stand-in of its HTTP API ─────────────────────────────


class FeishuStub:
    def __init__(self):
        self.docs = {"doc-ok": ["第一段", "第二段"]}
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if path.endswith("/tenant_access_token/internal"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "t", "expire": 7200}
            )
        if "/docx/v1/documents/doc-secret" in path:
            return httpx.Response(403, json={"code": 1770032, "msg": "forbidden"})
        if path == "/open-apis/docx/v1/documents" and request.method == "POST":
            self.docs["doc-new"] = []
            return httpx.Response(
                200, json={"code": 0, "data": {"document": {"document_id": "doc-new"}}}
            )
        if path.endswith("/children") and request.method == "POST":
            doc = path.split("/")[5]
            for child in json.loads(request.content)["children"]:
                key = next(k for k in child if k != "block_type")
                self.docs[doc].append(child[key]["elements"][0]["text_run"]["content"])
            return httpx.Response(200, json={"code": 0, "data": {}})
        if path.endswith("/blocks") and request.method == "GET":
            doc = path.split("/")[5]
            items = [
                {
                    "block_id": f"b{i}",
                    "block_type": 2,
                    "text": {"elements": [{"text_run": {"content": t}}]},
                }
                for i, t in enumerate(self.docs[doc])
            ]
            return httpx.Response(
                200, json={"code": 0, "data": {"items": items, "has_more": False}}
            )
        if path.startswith("/open-apis/docx/v1/documents/") and request.method == "GET":
            return httpx.Response(
                200, json={"code": 0, "data": {"document": {"title": "项目周报"}}}
            )
        if path.endswith("/metas/batch_query"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"metas": [{"url": "https://x.feishu.cn/docx/doc-new"}]},
                },
            )
        return httpx.Response(404, json={"code": 91402, "msg": "not found"})


@pytest.fixture
def feishu_stub(monkeypatch):
    stub = FeishuStub()
    transport = httpx.MockTransport(stub)
    monkeypatch.setattr(
        feishu.FeishuClient,
        "_http",
        lambda self: httpx.AsyncClient(
            base_url=self.settings.base, transport=transport
        ),
    )
    return stub


def test_feishu_read_create_edit_and_refusals(client, feishu_stub):
    person, project, room = _setup(client)
    r = client.post(
        "/me/integrations/feishu",
        json={"app_id": "cli_x", "app_secret": "s", "grants": [project]},
        headers=person,
    )
    assert r.status_code == 200, r.text
    fid = r.json()["data"]["id"]

    read = client.get(f"/integrations/{fid}/feishu/docs/doc-ok?topic={room}")
    assert read.status_code == 200, read.text
    assert [b["text"] for b in read.json()["data"]["blocks"]] == ["第一段", "第二段"]

    created = client.post(
        f"/integrations/{fid}/feishu/docs?topic={room}",
        json={"title": "周报", "content": "# 本周\n- 完成 12 项"},
    ).json()["data"]
    assert created["url"] == "https://x.feishu.cn/docx/doc-new"
    assert feishu_stub.docs["doc-new"] == ["本周", "完成 12 项"]

    edited = client.patch(
        f"/integrations/{fid}/feishu/docs/doc-new?topic={room}",
        json={"append": "阻碍：接口未定"},
    ).json()["data"]
    assert [b["text"] for b in edited["blocks"]][-1] == "阻碍：接口未定"

    refused = client.get(f"/integrations/{fid}/feishu/docs/doc-secret?topic={room}")
    assert refused.status_code == 403
    assert refused.json()["error"]["data"]["kind"] == "forbidden"

    no_search = client.post(
        f"/integrations/{fid}/feishu/search?topic={room}", json={"query": "周报"}
    )
    assert no_search.status_code == 403, "app-only credentials pretended to search"


def test_a_mail_server_on_the_platforms_own_network_is_refused(
    client, mailbox, monkeypatch
):
    monkeypatch.setattr(settings, "integration_allow_private_hosts", False)
    person, project, _room = _setup(client)
    for host, security in (
        ("127.0.0.1", "ssl"),
        ("localhost", "ssl"),
        ("10.0.0.5", "ssl"),
    ):
        r = client.post(
            "/me/integrations/mail",
            json={
                "imap_host": host,
                "smtp_host": host,
                "username": "a@x.test",
                "password": "p",
                "security": security,
            },
            headers=person,
        )
        assert r.status_code == 422 and "内网" in r.text, (host, r.text)
    plain = client.post(
        "/me/integrations/mail",
        json={
            "imap_host": "imap.qq.com",
            "smtp_host": "smtp.qq.com",
            "username": "a@x.test",
            "password": "p",
            "security": "plain",
        },
        headers=person,
    )
    assert plain.status_code == 422 and "加密" in plain.text
