"""What 问芝士 may do to the docs: search them, read a page, list the pages.

Three tools, in the shape OpenAI's documentation servers use (``search``,
``fetch``, ``list``): that is the shape a model has already seen in its
training data, so the prompt only has to say what they are for, not how to
drive them. It is also the shape the reader's question needs — one round of
retrieval cannot tell that 「那怎么把他移出去？」 is about the previous turn's
成员, while a model that can search again can.

Everything here reads the **public** index and the public ``.md`` twins, with
``library``'s ``dev=False``. 问芝士's answers are shown to anyone signed in, so
a developer page must never come back from one of these calls.

``Docs`` also remembers what the model looked at: ``read`` (pages it fetched)
and ``seen`` (sections it was shown, from a search or a fetch). The citation
rule and the final ``sources`` event are built from those two.
"""

import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from app.domain.docs_site import library, site
from app.domain.docs_site.retrieval import DocsIndex, Hit

logger = logging.getLogger(__name__)

MAX_RESULTS = 6
SUMMARY_CHARS = 140
HEADINGS = re.compile(r"(?m)^(?=## )")
# A section's own id, as the docs build writes it on the heading line.
SECTION_ID = re.compile(r"^## .*?\{#([a-z0-9-]+)\}")
# The fragment on a link the model was handed, e.g. /accept#is-merge.
FRAGMENT = re.compile(r"#([a-z0-9-]+)$")
LINK = re.compile(r"\[([^\]\n]*)\]\((/[^)\s]*)\)")

SEARCH_DOCS = {
    "type": "function",
    "function": {
        "name": "search_docs",
        "description": (
            "在知是的文档里按关键词搜索，返回最相关的几节：标题、小节名称、链接和一段摘录。"
            "短句、几个词、英文单词都可以。换一种说法再搜一次通常有用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索词，例如「邀请成员」「上传文件失败」。",
                }
            },
            "required": ["query"],
        },
    },
}

FETCH_DOC = {
    "type": "function",
    "function": {
        "name": "fetch_doc",
        "description": (
            "读一页文档的全文（Markdown）。链接可以带 #小节，例如 "
            "/teams#invite-member，这时只返回那一节和它前后的相邻小节。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "页面的地址或页名，如 accept、/accept#is-merge。",
                }
            },
            "required": ["url"],
        },
    },
}

LIST_DOCS = {
    "type": "function",
    "function": {
        "name": "list_docs",
        "description": "列出全部公开文档页：标题、地址和一句话说明。"
        "想知道有哪些页时用它。",
        "parameters": {"type": "object", "properties": {}},
    },
}

SCHEMAS = [SEARCH_DOCS, FETCH_DOC, LIST_DOCS]


def _excerpt(text: str, limit: int = SUMMARY_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _arguments(raw: str | dict | None) -> dict:
    """The tool call's arguments. Models send a JSON string; a malformed one is
    the model's mistake, not the reader's, so it comes back as ``{}`` and the
    executor says what was missing."""
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


@dataclass
class Docs:
    """The public docs, as one question's tool calls can reach them."""

    index: DocsIndex
    transport: httpx.AsyncBaseTransport | None = None
    # Pages the model fetched, in the order it fetched them: url -> payload.
    read: dict[str, dict] = field(default_factory=dict)
    # Sections it was shown, from a search or a fetch: url -> payload.
    seen: dict[str, dict] = field(default_factory=dict)
    # How many tool calls it made, whatever they returned.
    calls: int = 0
    # Whether any search came back with something.
    found: bool = False

    # ---------- the three tools ----------

    def search(self, query: str, limit: int = MAX_RESULTS) -> str:
        query = str(query or "").strip()
        if not query:
            return "search_docs 需要 query 参数（要搜的词）。"
        hits: list[Hit] = self.index.search(query, limit=limit)
        if hits:
            self.found = True
        for hit in hits:
            self.seen.setdefault(hit.section.url, self._payload(hit))
        return json.dumps(
            {
                "results": [
                    {
                        "title": h.section.title,
                        "heading": h.section.heading,
                        "url": h.section.url,
                        "excerpt": _excerpt(h.section.text, 300),
                    }
                    for h in hits
                ]
            },
            ensure_ascii=False,
        )

    def list_pages(self) -> str:
        pages: dict[str, dict] = {}
        for section in self.index.sections:
            page = section.url.split("#")[0]
            if page not in pages:
                pages[page] = {
                    "title": section.title,
                    "url": page,
                    "summary": _excerpt(section.text),
                }
        return json.dumps({"pages": list(pages.values())}, ensure_ascii=False)

    async def fetch(self, target: str) -> str:
        target = str(target or "").strip()
        if not target:
            return "fetch_doc 需要 url 参数（页面的地址或页名）。"
        slug = library.page_slug(target)
        if slug is None:
            return f"不是文档页：{target}。写页名（如 accept），或文档里的链接。"
        if slug.startswith("dev/"):
            return "开发文档不对外，只有公开的使用文档可以读。"
        try:
            text = await library.read_page(slug, dev=False, transport=self.transport)
        except Exception:  # noqa: BLE001 — a failed read is the model's to see, not the reader's
            logger.warning("docs assistant: reading %s failed", slug, exc_info=True)
            return f"读 {slug} 的时候出错了，换一页或再试一次。"
        if text is None:
            return f"没有这一页：{slug}。"
        self.read.setdefault(
            site.page_path(slug),
            {
                "title": self.page_title(slug),
                "heading": "",
                "url": site.page_path(slug),
            },
        )
        return self._section(text, target)

    async def call(self, name: str, raw_arguments: str | dict | None) -> str:
        """Run one tool call and hand back what the model gets to read."""
        self.calls += 1
        args = _arguments(raw_arguments)
        if name == "search_docs":
            return self.search(args.get("query", ""))
        if name == "fetch_doc":
            return await self.fetch(args.get("url", ""))
        if name == "list_docs":
            return self.list_pages()
        return f"没有这个工具：{name}。可以用的是 search_docs、fetch_doc、list_docs。"

    # ---------- what the reader is shown ----------

    def event(self, name: str, raw_arguments: str | dict | None) -> dict:
        """The progress line for one call: what the model is doing right now."""
        args = _arguments(raw_arguments)
        if name == "search_docs":
            return {"kind": "search", "query": str(args.get("query", ""))[:120]}
        if name == "fetch_doc":
            asked = str(args.get("url", ""))
            slug = library.page_slug(asked) or ""
            return {
                "kind": "fetch",
                "title": self.page_title(slug) if slug else asked[:120],
                "url": site.page_path(slug) if slug else asked[:200],
            }
        return {"kind": "list"}

    def page_title(self, slug: str) -> str:
        page = site.page_path(slug).split("#")[0]
        for section in self.index.sections:
            if section.url.split("#")[0] == page:
                return section.title
        return slug

    def sources(self, cited: list[str]) -> list[dict]:
        """What to show under the answer: the pages it read, then any search hit
        whose link survived in the answer."""
        out = list(self.read.values())
        known = {item["url"] for item in out}
        for url in cited:
            page = url.split("#")[0]
            if page in known:
                continue
            for seen_url, payload in self.seen.items():
                if seen_url.split("#")[0] == page:
                    out.append(
                        {
                            "title": payload["title"],
                            "heading": payload["heading"],
                            "url": seen_url,
                        }
                    )
                    known.add(page)
                    break
        return out

    @property
    def allowed_pages(self) -> set[str]:
        """Page paths the answer may link to, whatever the fragment: what a
        search returned, and what was read."""
        return {url.split("#")[0] for url in (*self.seen, *self.read)}

    def allows(self, url: str) -> bool:
        return url.split("#")[0] in self.allowed_pages

    # ---------- helpers ----------

    def _payload(self, hit: Hit) -> dict:
        return {
            "title": hit.section.title,
            "heading": hit.section.heading,
            "url": hit.section.url,
        }

    def _section(self, text: str, target: str) -> str:
        """The whole page, or — when the link names a section — that section and
        its neighbours, so the model gets the context around the answer without
        paying for the whole page."""
        fragment = FRAGMENT.search(target)
        if not fragment:
            return text
        want = fragment.group(1)
        blocks = HEADINGS.split(text)
        at = next(
            (
                i
                for i, b in enumerate(blocks)
                if (found := SECTION_ID.match(b)) and found.group(1) == want
            ),
            None,
        )
        if at is None:
            return text
        return "".join(blocks[max(at - 1, 0) : at + 2]).strip()


def cited_pages(answer: str) -> list[str]:
    """The links in an answer, in order, for the sources list."""
    return [url for _, url in LINK.findall(answer)]
