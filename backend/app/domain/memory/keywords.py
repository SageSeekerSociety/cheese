"""Query keywords for the flat DB memory backend's search.

The DB backend has no embeddings, so `cheese recall` cannot retrieve by
meaning. What it can do — and what this module implements — is degrade
*honestly*: cut a natural-language question into the keywords it is made of,
match any of them, and rank each memory by how much of the question it covers.
「CI 失败日志怎么看」 then reaches a fact about reading CI failure logs, which a
single ``ILIKE '%whole question%'`` never could.

Chinese has no spaces, so a CJK run is cut into character bigrams (the length
of most Chinese words) plus the run itself as a phrase term. This is a
structural transformation of the query string — no interpretation of what it
means (规则4). Which is also why nothing here is called semantic: see
``DbMemoryStore.search``.
"""

import re

# One token = a latin/digit word (identifiers, `gh-token`, `PostgreSQL`) or a
# run of CJK characters. Everything else (punctuation, spaces) separates.
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+#/-]*|[一-鿿㐀-䶿]+")

# Words that appear in almost any question and so discriminate nothing. Kept
# small on purpose: a wrongly-dropped keyword costs a miss, while a kept
# low-signal one only costs a little ranking noise.
_LATIN_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "why",
        "with",
        "you",
    }
)
_CJK_STOPWORDS = frozenset(
    {
        "怎么",
        "怎样",
        "如何",
        "什么",
        "为什",
        "么样",
        "哪个",
        "哪里",
        "哪些",
        "是不",
        "不是",
        "有没",
        "没有",
        "可以",
        "应该",
        "需要",
        "一个",
        "这个",
        "那个",
        "我们",
        "你们",
        "他们",
        "时候",
        "一下",
        "现在",
        "已经",
        "还是",
        "或者",
        "以及",
        "因为",
        "所以",
        "但是",
        "的话",
        "这样",
        "那样",
        "一些",
    }
)

# A latin word is a whole word, a bigram is a fragment — worth more.
_LATIN_WEIGHT = 2.0
_BIGRAM_WEIGHT = 1.0
# Longest CJK run still used as one phrase term. Past this it is almost never a
# term, just a sentence, and matching it whole is the old substring behaviour.
_PHRASE_MAX = 8
# Bounds the OR-clause a query can turn into.
_MAX_TERMS = 24
# Below this a match is a coincidence, not an answer — see `is_relevant`.
_MIN_COVERAGE = 0.15


def _add(terms: dict[str, float], term: str, weight: float) -> None:
    """Keep the strongest weight a term earned (a 2-char run is both phrase and
    bigram; the phrase reading wins)."""
    if terms.get(term, 0.0) < weight:
        terms[term] = weight


def query_terms(query: str, *, max_terms: int = _MAX_TERMS) -> list[tuple[str, float]]:
    """Split ``query`` into weighted keywords, strongest first.

    Empty when the query carries no usable keyword (all punctuation, or nothing
    but stopwords) — callers fall back to whole-string matching then.
    """
    terms: dict[str, float] = {}
    for raw in _TOKEN_RE.findall(query):
        if raw[0].isascii():
            token = raw.lower().strip("._-/+#")
            if len(token) < 2 or token in _LATIN_STOPWORDS:
                continue
            _add(terms, token, _LATIN_WEIGHT)
            continue
        if 2 <= len(raw) <= _PHRASE_MAX and raw not in _CJK_STOPWORDS:
            # A phrase that matches whole says much more than its bigrams do.
            _add(terms, raw, float(len(raw)))
        for i in range(len(raw) - 1):
            bigram = raw[i : i + 2]
            if bigram not in _CJK_STOPWORDS:
                _add(terms, bigram, _BIGRAM_WEIGHT)
    ranked = sorted(terms.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:max_terms]


def match_content(terms: list[tuple[str, float]], content: str) -> tuple[float, float]:
    """``(coverage, strongest)`` for one memory.

    ``coverage`` is how much of the query is in there, ``0.0..1.0``; 1.0 means
    every keyword matched, which is what an exact substring hit degenerates to,
    so the old behaviour still ranks top. ``strongest`` is the heaviest single
    term that matched — the difference between "shares a whole word with the
    question" and "shares two characters with it".
    """
    if not terms:
        return 0.0, 0.0
    lowered = content.lower()
    total = sum(weight for _, weight in terms)
    hit = [weight for term, weight in terms if term in lowered]
    return (sum(hit) / total if total else 0.0), max(hit, default=0.0)


def is_relevant(coverage: float, strongest: float) -> bool:
    """Whether a match is worth returning at all.

    Matching *any* keyword is a low bar: on a real 63-fact pool an unrelated
    question ("报销流程找谁审批") still shares a stray bigram with something and
    would come back looking like an answer. Two ways to clear the bar, because
    each covers what the other misses: cover enough of the question, or match
    one of its whole words — a long question containing `alembic` must still
    reach the alembic fact even if the rest of the sentence covers nothing.
    Thresholds read off this project's real pool: relevant hits scored
    0.19–0.56 there, coincidences 0.07–0.12.
    """
    return coverage >= _MIN_COVERAGE or strongest >= _LATIN_WEIGHT
