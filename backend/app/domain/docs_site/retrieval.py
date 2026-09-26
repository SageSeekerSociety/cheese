"""Find the sections of the public docs that answer a question.

The index is ``ask-index.json``, emitted by the docs build (docs/site/build.mjs)
and served by the frontend image next to the pages it was built from — so the
assistant answers from exactly the version readers see, and the backend image
carries no copy of the docs. Only public pages are in it: the answer is shown to
anyone signed in, so developer docs never reach it.

Ranking is BM25 over words (Latin script) and character bigrams (CJK), with a
section's page title and heading weighted into its text. A few hundred
sections need nothing heavier, and it answers in well under a millisecond.
"""

import logging
import math
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

REFRESH_SECONDS = 600
_WORD = re.compile(r"[a-z0-9_./-]+")
_CJK = re.compile(r"[㐀-鿿]+")
# Function words, question words and the product's own name: they appear in
# nearly every section, and inside a run of Chinese they would otherwise glue
# onto content words as junk bigrams (「怎么邀请」→「么邀」).
_STOP = re.compile(
    "知是|芝士|怎么办|怎么样|怎么|什么|为什么|如何|可以|能不能|有没有|是不是|"
    "一个|这个|那个|哪些|哪个|一下|自己|请问|帮我|给我|我们|你们|"
    "[吗呢吧啊的了是我你他她它们在和与及或就都也还要会能让把给被从到对里上下中请]"
)
# The words readers use for what the docs call something else.
_SYNONYMS = {
    "电脑": "设备",
    "机器": "设备",
    "服务器": "设备",
    "离线": "未连接",
    "同学": "成员",
    "同事": "成员",
    "队员": "成员",
    "小队": "团队",
    "作业": "提交",
    "交作业": "提交",
    "赛题": "题目",
    "没反应": "没有回复",
    "不回复": "没有回复",
    "不理我": "没有回复",
    "repo": "仓库",
    "github": "仓库",
    "代码库": "仓库",
    "合并": "采纳",
    "网页": "网站",
    "额度": "额度",
    "token": "额度",
}


def terms(text: str) -> list[str]:
    """Words for Latin script; overlapping bigrams for CJK runs.

    A lone CJK character stays itself. Stop phrases split a run rather than
    joining its neighbours."""
    text = text.lower()
    for word, canonical in _SYNONYMS.items():
        if word in text:
            text += f" {canonical}"
    out = [w.strip("./-") for w in _WORD.findall(text) if len(w.strip("./-")) > 1]
    for run in _CJK.findall(text):
        for part in _STOP.split(run):
            if len(part) == 1:
                out.append(part)
            else:
                out.extend(part[i : i + 2] for i in range(len(part) - 1))
    return out


@dataclass(frozen=True)
class Section:
    title: str
    heading: str
    url: str
    text: str


@dataclass
class Hit:
    section: Section
    score: float


@dataclass
class DocsIndex:
    sections: list[Section]
    _tf: list[Counter] = field(default_factory=list, repr=False)
    _len: list[int] = field(default_factory=list, repr=False)
    _df: Counter = field(default_factory=Counter, repr=False)
    _avg: float = 1.0

    def __post_init__(self) -> None:
        for s in self.sections:
            # Title and heading count twice: a section named for the question
            # is the best answer.
            tokens = terms(f"{s.title} {s.heading} " * 3 + s.text)
            tf = Counter(tokens)
            self._tf.append(tf)
            self._len.append(len(tokens))
            self._df.update(tf.keys())
        self._avg = (sum(self._len) / len(self._len)) if self._len else 1.0

    def search(
        self, question: str, *, page_url: str | None = None, limit: int = 5
    ) -> list[Hit]:
        q = set(terms(question))
        if not q:
            return []
        n = len(self.sections)
        k1, b = 1.4, 0.75
        hits: list[Hit] = []
        for i, s in enumerate(self.sections):
            tf, dl, score, matched = self._tf[i], self._len[i], 0.0, 0
            for t in q:
                f = tf.get(t)
                if not f:
                    continue
                matched += 1
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / self._avg))
            if not matched:
                continue
            # A question asked from a page is most likely about that page.
            if page_url and s.url.split("#")[0] == page_url:
                score *= 1.3
            # Coverage: sections matching more of the question's terms rank
            # above one lucky match.
            score *= 0.5 + 0.5 * matched / len(q)
            hits.append(Hit(s, score))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]


# Below this, the best section shares too little with the question to answer
# from; the assistant says the docs do not cover it instead of asking the model.
MIN_SCORE = 1.5


def relevant(hits: list[Hit]) -> list[Hit]:
    """The hits worth answering from.

    The best must clear MIN_SCORE; the rest must be within reach of it."""
    if not hits or hits[0].score < MIN_SCORE:
        return []
    return [h for h in hits if h.score >= hits[0].score * 0.35][:4]


class IndexSource:
    """The deployed index, fetched lazily and refreshed every ten minutes.

    A failed refresh keeps serving the copy it has: the docs changing is rare,
    the frontend being briefly unreachable during a deploy is not.
    """

    def __init__(
        self,
        url: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        cookies: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self._url = url
        self._transport = transport
        # For an index behind the /docs/dev/ gate: the pass, minted per fetch.
        self._cookies = cookies
        self._index: DocsIndex | None = None
        self._at = 0.0

    async def get(self) -> DocsIndex | None:
        if self._url and (
            self._index is None or time.monotonic() - self._at > REFRESH_SECONDS
        ):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(5.0), transport=self._transport
                ) as client:
                    r = await client.get(
                        self._url,
                        headers=_cookie_header(
                            self._cookies() if self._cookies else {}
                        ),
                    )
                    r.raise_for_status()
                    rows = r.json()
                self._index = DocsIndex(
                    [
                        Section(
                            str(x["title"]),
                            str(x.get("heading", "")),
                            str(x["url"]),
                            str(x["text"]),
                        )
                        for x in rows
                    ]
                )
            except Exception:  # noqa: BLE001 — keep the last good copy
                logger.warning(
                    "docs index refresh from %s failed", self._url, exc_info=True
                )
            self._at = time.monotonic()
        return self._index


def _cookie_header(cookies: dict[str, str]) -> dict[str, str]:
    if not cookies:
        return {}
    return {"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}


def _internal_pass() -> dict[str, str]:
    from app.domain.docs_site import access

    return {access.COOKIE: access.internal_pass()}


source = IndexSource(settings.docs_index_url)
# The developer pages, for agents in the platform's own project only
# (docs_site/library.py). Never read by 问芝士.
dev_source = IndexSource(settings.docs_dev_index_url, cookies=_internal_pass)
