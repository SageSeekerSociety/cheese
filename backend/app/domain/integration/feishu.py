"""Feishu / Lark documents over the open platform API.

Two ways in, both configured by the owner: the owner's own custom app
(app_id + app_secret, tenant token — sees the documents shared with the app),
or the owner's user authorization (sees what the owner sees, and is the only
way the search API answers). Every failure becomes ``IntegrationError`` with a
kind: expired or missing authorization, no permission on that document, not
found, or the service failing.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

from app.domain.integration.mail import IntegrationError

DOMAINS = {"feishu": "https://open.feishu.cn", "lark": "https://open.larksuite.com"}

#: Codes the open platform answers with, by what the caller can do about them.
AUTH_CODES = {99991661, 99991663, 99991664, 99991665, 99991668, 99991677, 20005}
FORBIDDEN_CODES = {99991672, 99991679, 1770032, 1770031, 91204, 131006, 1061004}
NOT_FOUND_CODES = {1770002, 91402, 131005, 1061003}

BLOCK_TEXT, BLOCK_H1, BLOCK_H2, BLOCK_H3, BLOCK_BULLET, BLOCK_ORDERED = (
    2,
    3,
    4,
    5,
    12,
    13,
)
BLOCK_KEY = {
    2: "text",
    3: "heading1",
    4: "heading2",
    5: "heading3",
    12: "bullet",
    13: "ordered",
}


@dataclass
class FeishuSettings:
    app_id: str
    app_secret: str
    domain: str = "feishu"
    user_access_token: str | None = None
    user_token_expires_at: float | None = None
    refresh_token: str | None = None
    #: Folders the app may list when there is no user authorization to search with.
    folders: list[str] = field(default_factory=list)

    @property
    def base(self) -> str:
        return DOMAINS.get(self.domain, DOMAINS["feishu"])


def _raise(status: int, body: dict | None, action: str) -> None:
    code = (body or {}).get("code")
    msg = (body or {}).get("msg") or (body or {}).get("message") or ""
    detail = f"{action}：飞书返回 {code if code is not None else status} {msg}".strip()
    if status == 401 or code in AUTH_CODES:
        raise IntegrationError(
            "auth_failed", f"{detail}。授权已失效，到「我的连接」里重新授权"
        )
    if status == 403 or code in FORBIDDEN_CODES:
        raise IntegrationError(
            "forbidden",
            f"{detail}。没有这份文档的权限：把文档分享给应用或授权账号后再试",
        )
    if status == 404 or code in NOT_FOUND_CODES:
        raise IntegrationError("not_found", f"{detail}。找不到这份文档")
    raise IntegrationError("error", detail)


class FeishuClient:
    def __init__(
        self,
        settings: FeishuSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings
        self._transport = transport
        self._tenant: tuple[str, float] | None = None

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.settings.base, timeout=20, transport=self._transport
        )

    async def _call(
        self, method: str, path: str, action: str, *, token: str | None = None, **kwargs
    ) -> dict:
        headers = {"Authorization": f"Bearer {token or await self.token()}"}
        try:
            async with self._http() as http:
                response = await http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise IntegrationError(
                "unreachable", f"{action}：连不上飞书（{exc}）"
            ) from exc
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code >= 400 or (body or {}).get("code", 0) != 0:
            _raise(response.status_code, body, action)
        return (body or {}).get("data") or {}

    async def tenant_token(self) -> str:
        if self._tenant and self._tenant[1] > time.time() + 60:
            return self._tenant[0]
        try:
            async with self._http() as http:
                response = await http.post(
                    "/open-apis/auth/v3/tenant_access_token/internal",
                    json={
                        "app_id": self.settings.app_id,
                        "app_secret": self.settings.app_secret,
                    },
                )
        except httpx.HTTPError as exc:
            raise IntegrationError("unreachable", f"连不上飞书（{exc}）") from exc
        body = response.json() if response.content else {}
        if response.status_code >= 400 or body.get("code", 0) != 0:
            _raise(response.status_code, body, "用应用凭据取令牌")
        token = body["tenant_access_token"]
        self._tenant = (token, time.time() + int(body.get("expire", 7200)))
        return token

    async def token(self) -> str:
        """The owner's own authorization when there is one, else the app's."""
        if self.settings.user_access_token:
            if (self.settings.user_token_expires_at or 0) <= time.time() + 60:
                await self.refresh_user_token()
            return self.settings.user_access_token or ""
        return await self.tenant_token()

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        query = urlencode(
            {
                "app_id": self.settings.app_id,
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
        return f"{self.settings.base}/open-apis/authen/v1/authorize?{query}"

    async def _user_token(self, grant: dict, action: str) -> None:
        try:
            async with self._http() as http:
                response = await http.post(
                    "/open-apis/authen/v2/oauth/token",
                    json={
                        "client_id": self.settings.app_id,
                        "client_secret": self.settings.app_secret,
                        **grant,
                    },
                )
        except httpx.HTTPError as exc:
            raise IntegrationError(
                "unreachable", f"{action}：连不上飞书（{exc}）"
            ) from exc
        body = response.json() if response.content else {}
        if (
            response.status_code >= 400
            or body.get("code", 0) != 0
            or "access_token" not in body
        ):
            _raise(response.status_code, body, action)
        self.settings.user_access_token = body["access_token"]
        self.settings.user_token_expires_at = time.time() + int(
            body.get("expires_in", 7200)
        )
        self.settings.refresh_token = (
            body.get("refresh_token") or self.settings.refresh_token
        )

    async def exchange_code(self, code: str, redirect_uri: str) -> None:
        await self._user_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            "换取用户授权",
        )

    async def refresh_user_token(self) -> None:
        if not self.settings.refresh_token:
            raise IntegrationError(
                "auth_failed", "飞书用户授权已过期，到「我的连接」里重新授权"
            )
        await self._user_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": self.settings.refresh_token,
            },
            "刷新用户授权",
        )

    # ── documents ──────────────────────────────────────────────────────────

    async def search(self, query: str, limit: int = 20) -> list[dict]:
        if self.settings.user_access_token:
            data = await self._call(
                "POST",
                "/open-apis/suite/docs-api/search/object",
                "搜索文档",
                json={
                    "search_key": query,
                    "count": limit,
                    "offset": 0,
                    "docs_types": ["docx", "doc"],
                },
            )
            return [
                {
                    "document_id": d.get("docs_token"),
                    "title": d.get("title"),
                    "type": d.get("docs_type"),
                }
                for d in data.get("docs_entities", [])
            ]
        if not self.settings.folders:
            raise IntegrationError(
                "forbidden",
                "只用应用凭据时飞书不提供全文搜索：在「我的连接」里授权个人账号，或登记应用能看到的文件夹",
            )
        found = []
        for folder in self.settings.folders:
            data = await self._call(
                "GET",
                "/open-apis/drive/v1/files",
                "列出文件夹",
                params={"folder_token": folder, "page_size": 200},
            )
            for f in data.get("files", []):
                if (
                    f.get("type") == "docx"
                    and query.lower() in (f.get("name") or "").lower()
                ):
                    found.append(
                        {
                            "document_id": f.get("token"),
                            "title": f.get("name"),
                            "type": "docx",
                            "url": f.get("url"),
                        }
                    )
        return found[:limit]

    async def url(self, document_id: str) -> str | None:
        data = await self._call(
            "POST",
            "/open-apis/drive/v1/metas/batch_query",
            "取文档链接",
            json={
                "request_docs": [{"doc_token": document_id, "doc_type": "docx"}],
                "with_url": True,
            },
        )
        metas = data.get("metas") or []
        return metas[0].get("url") if metas else None

    async def blocks(self, document_id: str) -> list[dict]:
        items: list[dict] = []
        page = None
        while True:
            params = {"page_size": 500, **({"page_token": page} if page else {})}
            data = await self._call(
                "GET",
                f"/open-apis/docx/v1/documents/{document_id}/blocks",
                "读取文档",
                params=params,
            )
            items += data.get("items", [])
            if not data.get("has_more"):
                return items
            page = data.get("page_token")

    async def read(self, document_id: str) -> dict:
        meta = await self._call(
            "GET", f"/open-apis/docx/v1/documents/{document_id}", "读取文档"
        )
        blocks = await self.blocks(document_id)
        paragraphs = []
        unsupported = 0
        for block in blocks:
            kind = block.get("block_type") or 0
            key = BLOCK_KEY.get(kind)
            if key is None:
                if kind not in (1,):
                    unsupported += 1
                continue
            text = "".join(
                (e.get("text_run") or {}).get("content", "")
                for e in (block.get(key) or {}).get("elements", [])
            )
            paragraphs.append(
                {"block_id": block.get("block_id"), "kind": key, "text": text}
            )
        return {
            "document_id": document_id,
            "title": (meta.get("document") or {}).get("title"),
            "url": await self.url(document_id),
            "blocks": paragraphs,
            "unsupported_blocks": unsupported,
        }

    @staticmethod
    def blocks_from_markdown(text: str) -> list[dict]:
        children = []
        for line in text.splitlines():
            if not line.strip():
                continue
            kind, content = BLOCK_TEXT, line
            for prefix, block in (
                ("### ", BLOCK_H3),
                ("## ", BLOCK_H2),
                ("# ", BLOCK_H1),
            ):
                if line.startswith(prefix):
                    kind, content = block, line[len(prefix) :]
                    break
            else:
                if re.match(r"^\s*[-*] ", line):
                    kind, content = BLOCK_BULLET, re.sub(r"^\s*[-*] ", "", line)
                elif re.match(r"^\s*\d+[.、] ", line):
                    kind, content = BLOCK_ORDERED, re.sub(r"^\s*\d+[.、] ", "", line)
            children.append(
                {
                    "block_type": kind,
                    BLOCK_KEY[kind]: {"elements": [{"text_run": {"content": content}}]},
                }
            )
        return children

    async def append(self, document_id: str, markdown: str) -> int:
        children = self.blocks_from_markdown(markdown)
        for start in range(0, len(children), 50):
            await self._call(
                "POST",
                f"/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children",
                "往文档里追加内容",
                json={"children": children[start : start + 50], "index": -1},
            )
        return len(children)

    async def replace_text(self, document_id: str, block_id: str, text: str) -> None:
        await self._call(
            "PATCH",
            f"/open-apis/docx/v1/documents/{document_id}/blocks/{block_id}",
            "修改文档段落",
            json={
                "update_text_elements": {"elements": [{"text_run": {"content": text}}]}
            },
        )

    async def create(
        self, title: str, markdown: str, folder: str | None = None
    ) -> dict:
        data = await self._call(
            "POST",
            "/open-apis/docx/v1/documents",
            "新建文档",
            json={"title": title, **({"folder_token": folder} if folder else {})},
        )
        document_id = (data.get("document") or {}).get("document_id")
        if not document_id:
            raise IntegrationError("error", "飞书没有返回新文档的 id")
        written = await self.append(document_id, markdown) if markdown.strip() else 0
        return {
            "document_id": document_id,
            "url": await self.url(document_id),
            "blocks_written": written,
        }
