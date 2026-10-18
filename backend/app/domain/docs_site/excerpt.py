"""One excerpt rule for the docs: collapse whitespace, cut at ``limit``, mark the cut.

The manual's search results (``library``) and the 问芝士 tools (``tools``) both
show a section as a short line. They want different lengths, so ``limit`` has no
default here: each caller says how long its line is, and the rule for how a line
is cut stays in one place.
"""


def excerpt(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"
