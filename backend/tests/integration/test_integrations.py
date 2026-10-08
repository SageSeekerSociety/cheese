"""A mailbox lent to a project: its teammates read and draft, only the owner
sends, and what is sent is exactly the draft the owner read.

The IMAP/SMTP layer is replaced by a recording stand-in here; the protocol
itself is exercised against a real mail server in `test_mail_server.py`.
"""

import asyncio
import json
import time
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core.config import settings
from app.domain.integration import feishu, mail
from app.domain.integration.mail import IntegrationError
from app.domain.integration.models import Integration
from app.domain.integration.service import seal, unseal
from app.domain.library import service as library
from app.domain.user.repositories import UserRepository
from tests.conftest import seed_user
from tests.integration.conftest import (
    post_project,
    room_agent_seat,
    session_auth_headers,
)

OWNER = "user-1"
#: The platform administrator of these tests. Handle-only: the admin page's gate
#: reads the allow-list, and the tests that need a DB user make their own.
ADMIN = "admin-1"
#: The one secret an administrator types; it must never come back out.
PLATFORM_SECRET = "platform-secret-9"


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
    r = post_project(client, json={"name": f"连接-{uuid.uuid4().hex[:6]}"}, owner=OWNER)
    project = r.json()["data"]["id"]
    room = client.post(
        "/topics",
        json={"project_id": project, "title": "对外沟通"},
        headers=session_auth_headers(OWNER),
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


def test_the_room_line_names_the_teammate_that_drafted(client, mailbox):
    """The line under a draft names whoever wrote it, the way a mention chip
    does, so a teammate called Nova is not announced as 「芝士」."""
    person, project, room = _setup(client)
    row = _connect(client, person, [project])
    seat = room_agent_seat(client, room)

    assert _draft(client, row, room).status_code == 200
    [card] = _room_events(client, room, person, "mail_drafted")
    assert card["content"].startswith(f"<@{seat}> "), card["content"]
    assert "芝士" not in card["content"]


def _room_events(client, room, person, event_type):
    blocks = client.get(f"/topics/{room}/blocks", headers=person).json()["data"]
    rows = blocks["data"] if isinstance(blocks, dict) else blocks
    return [b for b in rows if (b.get("meta") or {}).get("event_type") == event_type]


def test_the_room_card_shows_the_draft_and_learns_how_it_ended(client, mailbox):
    """chiruotong, 2026-09-27: confirming in 「我的连接」 is too far away. The
    room shows the whole draft where it was written, the owner is told where to
    find it, and the card learns the outcome."""
    person, project, room = _setup(client)
    row = _connect(client, person, [project])

    first = _draft(client, row, room).json()["data"]
    [card] = _room_events(client, room, person, "mail_drafted")
    mail = card["meta"]["mail"]
    assert mail["owner"] == OWNER
    assert mail["to"] == ["bob@x.test"]
    assert mail["subject"] == "第三季度预算"
    assert mail["body"] == "附件是核对后的预算表。"
    assert card["meta"]["mail_draft_id"] == first["id"]

    inbox = client.get(f"/projects/{project}/alerts", headers=person).json()["data"][
        "data"
    ]
    [told] = [n for n in inbox if "邮件草稿待你确认" in n["title"]]
    assert told["topic_id"] == room
    assert "房间" in told["body"]

    client.post(f"/me/mail-drafts/{first['id']}/send", headers=person)
    second = _draft(client, row, room).json()["data"]
    client.post(f"/me/mail-drafts/{second['id']}/discard", headers=person)

    outcomes = {
        e["meta"]["mail_draft_id"]: e["meta"]["status"]
        for e in _room_events(client, room, person, "mail_result")
    }
    assert outcomes == {first["id"]: "sent", second["id"]: "discarded"}


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
        self.no_drive_scope = False
        #: Answer an authorization-code exchange the way Feishu refuses a bad code.
        self.refuse_code = False
        #: Every OAuth token exchange this stub was asked for, in order.
        self.tokens: list[dict] = []
        #: The bearer token each document call carried, in order. The first one
        #: is the app's tenant token when the connection has no user
        #: authorization of its own, and the member's token when it has.
        self.bearers: list[str] = []
        #: The (app_id, app_secret) each tenant-token call carried.
        self.app_credentials: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if path.endswith("/tenant_access_token/internal"):
            body = json.loads(request.content)
            self.app_credentials.append((body["app_id"], body["app_secret"]))
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "t", "expire": 7200}
            )
        if path == "/open-apis/authen/v2/oauth/token":
            body = json.loads(request.content)
            self.tokens.append(body)
            if self.refuse_code and body.get("grant_type") == "authorization_code":
                return httpx.Response(400, json={"code": 20003, "msg": "invalid code"})
            # A different token each time, so "the refreshed one came back and was
            # used" is visible in the document calls rather than identical strings.
            fresh = "u1" if body.get("grant_type") == "authorization_code" else "u2"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "access_token": fresh,
                    "refresh_token": f"r-{fresh}",
                    "expires_in": 7200,
                },
            )
        if path.startswith("/open-apis/docx") or path.endswith(
            ("/metas/batch_query", "/suite/docs-api/search/object", "/drive/v1/files")
        ):
            self.bearers.append(
                request.headers["Authorization"].removeprefix("Bearer ")
            )
        if path.endswith("/suite/docs-api/search/object"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "docs_entities": [
                            {
                                "docs_token": "doc-ok",
                                "docs_type": "docx",
                                "title": "项目周报",
                            }
                        ]
                    },
                },
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
            if self.no_drive_scope:
                return httpx.Response(
                    400, json={"code": 99991672, "msg": "Access denied. drive"}
                )
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


def _configure_platform_app(client, secret=PLATFORM_SECRET, admin=ADMIN):
    """平台管理员在后台填一次应用（`PUT /admin/integrations/feishu`）。"""
    r = client.put(
        "/admin/integrations/feishu",
        json={"app_id": "cli_platform", "app_secret": secret, "domain": "feishu"},
        headers=session_auth_headers(admin),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _authorize(client, person, row_id, code="the-code"):
    """「连接飞书」的第二步：拿授权地址、走回调，把 token 存进自己那一行。"""
    url = client.get(
        f"/me/integrations/{row_id}/feishu/authorize", headers=person
    ).json()["data"]["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    back = client.get(
        "/integrations/feishu/callback",
        params={"code": code, "state": state},
        follow_redirects=False,
    )
    assert back.status_code == 302, back.text
    assert "feishu=ok" in back.headers["location"], back.headers["location"]
    return url


def _shared_app_connection(client, person, project, *, admin=ADMIN, monkeypatch=None):
    """一个人的连接：管理员配应用 → 成员点「连接飞书」→ 回调 → 勾选项目。"""
    if monkeypatch is not None:
        monkeypatch.setattr(settings, "platform_admin_handles", [admin])
    _configure_platform_app(client, admin=admin)
    r = client.post("/me/integrations/feishu", headers=person)
    assert r.status_code == 200, r.text
    row = r.json()["data"]
    _authorize(client, person, row["id"])
    client.patch(
        f"/me/integrations/{row['id']}", json={"grants": [project]}, headers=person
    )
    return row


def _seed_own_app_connection(
    client, handle, grants, *, app_id="cli_own", app_secret="own-secret", token=None
):
    """一条自带凭据的连接，就是「每个人自己建应用」那个年代留在库里的行。

    它是**数据**，不是某个接口还能建出来的东西：新流程下没人再写这样的行，而库里
    已有的照旧要能用。所以这里直接落库，不经过任何路由。
    """

    async def _seed() -> str:
        user = None
        async with client.test_factory() as s:
            user = await UserRepository(s).get_by_username(handle)
            assert user is not None
            row = Integration(
                id=uuid.uuid4(),
                owner_user_id=user.id,
                owner_handle=handle,
                provider="feishu",
                label="飞书",
                config={"app_id": app_id, "domain": "feishu", "folders": []},
                grants=[str(g) for g in grants],
                status="ok",
            )
            secret: dict = {"app_secret": app_secret}
            if token:
                secret |= {
                    "user_access_token": token,
                    "user_token_expires_at": time.time() + 3600,
                    "refresh_token": "own-refresh",
                }
            seal(row, secret)
            s.add(row)
            await s.commit()
            return str(row.id)

    return asyncio.run(_seed())


def _expire_user_token(client, row_id):
    """把本人那份 token 的到期时间推到过去，逼出一次 refresh。"""

    async def _expire() -> None:
        async with client.test_factory() as s:
            row = await s.get(Integration, uuid.UUID(row_id))
            assert row is not None
            secret = unseal(row)
            secret["user_token_expires_at"] = time.time() - 10
            seal(row, secret)
            await s.commit()

    asyncio.run(_expire())


# ── 平台管理员配的那一个应用 ───────────────────────────────────────────────


def test_only_a_platform_admin_reads_or_writes_the_app(
    client, monkeypatch, feishu_stub
):
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    person, _project, _room = _setup(client)
    body = {"app_id": "cli_platform", "app_secret": PLATFORM_SECRET}

    assert client.get("/admin/integrations/feishu", headers=person).status_code == 403
    assert (
        client.put(
            "/admin/integrations/feishu",
            json=body,
            headers=person,
        ).status_code
        == 403
    )

    saved = _configure_platform_app(client)
    assert saved["configured"] is True and saved["app_id"] == "cli_platform"
    assert client.get("/admin/integrations/feishu").status_code in (401, 403)


def test_the_app_secret_is_written_but_never_read_back(
    client, monkeypatch, feishu_stub
):
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    person, _project, _room = _setup(client)
    _configure_platform_app(client)

    read = client.get("/admin/integrations/feishu", headers=session_auth_headers(ADMIN))
    assert read.status_code == 200, read.text
    assert read.json()["data"]["configured"] is True
    assert PLATFORM_SECRET not in read.text, "the secret came back on the page"

    # Saving without retyping the secret keeps the one already stored — it is a
    # write-only field, so an administrator changing only the domain never has to
    # paste it again. Whether it kept it is checked where the value is used: the
    # token exchange below.
    again = client.put(
        "/admin/integrations/feishu",
        json={"app_id": "cli_platform", "domain": "lark"},
        headers=session_auth_headers(ADMIN),
    )
    assert again.status_code == 200, again.text
    assert again.json()["data"]["domain"] == "lark"

    row = client.post("/me/integrations/feishu", headers=person).json()["data"]
    url = _authorize(client, person, row["id"])
    assert "cli_platform" in url and "open.larksuite.com" in url
    assert feishu_stub.tokens[-1]["grant_type"] == "authorization_code"
    assert feishu_stub.tokens[-1]["client_id"] == "cli_platform"
    assert feishu_stub.tokens[-1]["client_secret"] == PLATFORM_SECRET


def test_authorizing_before_an_administrator_configures_says_so(client, feishu_stub):
    person, _project, _room = _setup(client)
    availability = client.get("/me/integrations/feishu", headers=person)
    assert availability.status_code == 200, availability.text
    assert availability.json()["data"]["configured"] is False

    r = client.post("/me/integrations/feishu", headers=person)
    assert r.status_code == 422, r.text
    assert "管理员还没配置飞书应用" in r.text
    assert client.get("/me/integrations", headers=person).json()["data"]["data"] == []


def _feishu_landing(client, **params) -> dict[str, list[str]]:
    back = client.get(
        "/integrations/feishu/callback", params=params, follow_redirects=False
    )
    assert back.status_code == 302, back.text
    return parse_qs(urlparse(back.headers["location"]).query)


def test_the_callback_lands_with_an_outcome_code(client, feishu_stub, monkeypatch):
    """「我的连接」says how authorizing went in its reader's language: the
    address carries a code, and what Feishu itself said in Feishu's words."""
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    person, _project, _room = _setup(client)
    _configure_platform_app(client)
    row = client.post("/me/integrations/feishu", headers=person).json()["data"]
    url = client.get(
        f"/me/integrations/{row['id']}/feishu/authorize", headers=person
    ).json()["data"]["url"]
    state = parse_qs(urlparse(url).query)["state"][0]

    assert _feishu_landing(client, code="c", state="forged.state.x") == {
        "feishu": ["invalid_link"]
    }
    assert _feishu_landing(client, state=state, error="access_denied") == {
        "feishu": ["denied"],
        "feishu_detail": ["access_denied"],
    }
    feishu_stub.refuse_code = True
    assert _feishu_landing(client, code="c", state=state) == {
        "feishu": ["exchange_failed"],
        "feishu_detail": ["20003 invalid code"],
    }


def test_the_callback_keeps_the_members_own_tokens(client, feishu_stub, monkeypatch):
    person, project, room = _setup(client)
    row = _shared_app_connection(client, person, project, monkeypatch=monkeypatch)
    assert row["shared_app"] is True

    listed = client.get("/me/integrations", headers=person).json()["data"]["data"]
    assert listed[0]["user_authorized"] is True
    assert PLATFORM_SECRET not in str(listed)

    read = client.get(f"/integrations/{row['id']}/feishu/docs/doc-ok?topic={room}")
    assert read.status_code == 200, read.text
    assert feishu_stub.bearers[-1] == "u1", "the member's token was not the one used"


def test_an_expired_member_token_is_refreshed_before_use(
    client, feishu_stub, monkeypatch
):
    person, project, room = _setup(client)
    row = _shared_app_connection(client, person, project, monkeypatch=monkeypatch)
    _expire_user_token(client, row["id"])

    read = client.get(f"/integrations/{row['id']}/feishu/docs/doc-ok?topic={room}")
    assert read.status_code == 200, read.text
    assert [t["grant_type"] for t in feishu_stub.tokens[-2:]] == [
        "authorization_code",
        "refresh_token",
    ]
    assert feishu_stub.bearers[-1] == "u2", "the refreshed token was not the one used"

    # Written back: a second call does not refresh again, and the refresh token
    # from the exchange (a one-time value at Feishu) is the one it would send.
    client.get(f"/integrations/{row['id']}/feishu/docs/doc-ok?topic={room}")
    assert [t["grant_type"] for t in feishu_stub.tokens] == [
        "authorization_code",
        "refresh_token",
    ]
    assert feishu_stub.tokens[-1]["refresh_token"] == "own-refresh" or (
        feishu_stub.tokens[-1]["refresh_token"] == "r-u1"
    )


def test_a_member_token_that_cannot_be_refreshed_names_the_way_back(
    client, feishu_stub, monkeypatch
):
    person, project, room = _setup(client)
    row = _shared_app_connection(client, person, project, monkeypatch=monkeypatch)

    async def _drop_refresh_token() -> None:
        async with client.test_factory() as s:
            fresh = await s.get(Integration, uuid.UUID(row["id"]))
            secret = unseal(fresh)
            secret["user_token_expires_at"] = time.time() - 10
            secret["refresh_token"] = None
            seal(fresh, secret)
            await s.commit()

    asyncio.run(_drop_refresh_token())
    r = client.get(f"/integrations/{row['id']}/feishu/docs/doc-ok?topic={room}")
    assert r.status_code == 401, r.text
    assert "授权已失效，到「我的连接」里重新授权" in r.text


def test_a_connection_with_its_own_app_credentials_still_works(
    client, feishu_stub, monkeypatch
):
    """没有平台应用，老式连接照样读、写、搜 —— 它自带的那套凭据说了算。"""
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    person, project, room = _setup(client)
    assert (
        client.get("/me/integrations/feishu", headers=person).json()["data"][
            "configured"
        ]
        is False
    )
    fid = _seed_own_app_connection(
        client,
        OWNER,
        [project],
        app_id="cli_own",
        app_secret="own-secret",
        token="own-token",
    )

    read = client.get(f"/integrations/{fid}/feishu/docs/doc-ok?topic={room}")
    assert read.status_code == 200, read.text
    assert [b["text"] for b in read.json()["data"]["blocks"]] == ["第一段", "第二段"]
    assert feishu_stub.bearers[-1] == "own-token"

    found = client.post(
        f"/integrations/{fid}/feishu/search?topic={room}", json={"query": "周报"}
    )
    assert found.status_code == 200, found.text
    assert found.json()["data"]["documents"][0]["document_id"] == "doc-ok"

    listed = [
        r
        for r in client.get("/me/integrations", headers=person).json()["data"]["data"]
        if r["id"] == fid
    ]
    assert listed[0]["shared_app"] is False


def test_an_app_only_connection_cannot_search_and_says_which_way(
    client, feishu_stub, monkeypatch
):
    """只用应用凭据、主人又还没授权个人账号：读得了、搜不了。

    这不是回归 —— 飞书的搜索接口只认用户 token，拿应用凭据去搜只会得到空结果，
    所以这里宁可回一句「差的是个人授权」。连接照旧能用（读、写、按文件夹列）。
    """
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    _person, project, room = _setup(client)
    fid = _seed_own_app_connection(client, OWNER, [project])

    read = client.get(f"/integrations/{fid}/feishu/docs/doc-ok?topic={room}")
    assert read.status_code == 200, read.text

    r = client.post(
        f"/integrations/{fid}/feishu/search?topic={room}", json={"query": "周报"}
    )
    assert r.status_code == 403, r.text
    assert "授权个人账号" in r.text
    assert feishu_stub.bearers[-1] == "t", "a user token was used without one"


def test_feishu_read_create_edit_and_refusals(client, feishu_stub, monkeypatch):
    person, project, room = _setup(client)
    fid = _shared_app_connection(client, person, project, monkeypatch=monkeypatch)["id"]

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


def test_an_app_without_the_drive_scope_still_reads_and_writes(
    client, feishu_stub, monkeypatch
):
    """dev, 2026-09-27: the document scopes were granted, the drive metadata one
    was not, and the whole read failed on the link lookup at the end."""
    feishu_stub.no_drive_scope = True
    person, project, room = _setup(client)
    fid = _shared_app_connection(client, person, project, monkeypatch=monkeypatch)["id"]

    read = client.get(f"/integrations/{fid}/feishu/docs/doc-ok?topic={room}")
    assert read.status_code == 200, read.text
    data = read.json()["data"]
    assert [b["text"] for b in data["blocks"]] == ["第一段", "第二段"]
    assert data["url"] is None

    created = client.post(
        f"/integrations/{fid}/feishu/docs?topic={room}",
        json={"title": "周报", "content": "本周"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["data"]["document_id"] == "doc-new"


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
