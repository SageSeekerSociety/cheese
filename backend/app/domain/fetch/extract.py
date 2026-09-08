"""HTML → markdown, deliberately without guessing at "the main content".

Every readability-style extractor scores blocks by text density and link ratio
and throws away what scores low. On article pages that works. On list pages —
a directory, an index, a forum front page — the whole page IS short entries and
links, so the scorer throws away the page. Measured on one such page (918 KB of
HTML): readability returned 622 characters and trafilatura 465, while converting
the DOM without scoring it returned 24,304. The page was not hard to read; the
extractors decided it was navigation.

So this module strips what is never content (script, style, noscript, iframe,
svg) and converts the rest. It keeps boilerplate the scorers would have removed,
and that is the trade being made on purpose: a nav bar costs tokens, a discarded
page costs the answer.
"""

from __future__ import annotations

import re

# Tags whose text is never prose. Removed before conversion rather than after:
# a 918 KB page carrying a 149 KB <script> converts to 24k characters once the
# script is gone, and the conversion itself then costs well under a second.
_NEVER_CONTENT = ("script", "style", "noscript", "iframe", "svg", "template")
_STRIP_RE = re.compile(
    r"<(%s)\b[^>]*>.*?</\1>" % "|".join(_NEVER_CONTENT),
    re.DOTALL | re.IGNORECASE,
)
_BLANK_RUN = re.compile(r"\n{3,}")

#: Converting a page far larger than this buys nothing an answer needs, and the
#: cost is paid in latency on a page that is usually a mirror or a dump.
MAX_HTML_BYTES = 4 * 1024 * 1024


def strip_non_content(html: str) -> str:
    """Drop the tags whose contents are never prose."""
    return _STRIP_RE.sub(" ", html)


def to_markdown(html: str) -> str:
    """Convert a page to markdown without deciding what its "real" content is.

    Falls back to tag-stripping if the markdown converter is unavailable or
    raises: a rough plaintext rendering still answers most prompts, while an
    exception here would turn a readable page into a failed fetch.
    """
    if len(html) > MAX_HTML_BYTES:
        html = html[:MAX_HTML_BYTES]
    cleaned = strip_non_content(html)
    try:
        from markdownify import markdownify

        text = markdownify(cleaned)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", cleaned)
    return _BLANK_RUN.sub("\n\n", text).strip()


def substantive_length(markdown: str) -> int:
    """How much of this is prose, as opposed to a wall of links.

    A blocked page and a real page are both "200 OK with bytes"; what separates
    them is whether anything reads like a sentence. Counting only lines that are
    long and are not bare links is what lets a caller tell "the site returned a
    login wall" from "the site returned an article" without keyword matching —
    which was measurably wrong: a page carrying 166 KB of real content was
    graded as blocked because the word "登录" appeared in its header.
    """
    return sum(
        len(line)
        for raw in markdown.splitlines()
        if len(line := raw.strip()) > 40 and not line.startswith(("[", "!", "|", "*"))
    )
