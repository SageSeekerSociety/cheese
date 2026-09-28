"""把一段话切成它的关键词，按权重排。

`cheese_recall` 撤销之后，这里只剩一个调用方：写记忆之前拿几个最重的词去检出目录
里查一遍（`redundant.py`）——「这件事 repo 里是不是已经写着了」（结论 61）。判据
要有，是因为提示词里的一句叮嘱只在模型愿意照做的时候成立，而写入端每天要挡的正是
它没照做的那些次。

切法是结构性的，不含对意思的解读（规则4）：一段话切成它由之组成的词。中文没有
空格，所以一段 CJK 串切成字符二元组（多数中文词的长度），外加整串本身作为一个
短语词——短语整段命中说明的事情比它的二元组多。
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
