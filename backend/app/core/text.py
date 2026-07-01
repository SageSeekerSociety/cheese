"""Small text helpers shared across domains."""

import re

_WS = re.compile(r"\s+")
# Paired markdown markers whose opener, if left unclosed by a hard truncation,
# would render as literal `**` / `` ` `` / `~~`. Order matters: `**` before `*`.
_PAIRED = ("**", "~~", "`")


def _drop_unclosed(text: str, marker: str) -> str:
    """If `marker` appears an odd number of times, remove its last (unpaired)
    occurrence so the preview has no dangling opener."""
    if text.count(marker) % 2 == 1:
        idx = text.rfind(marker)
        text = text[:idx] + text[idx + len(marker) :]
    return text


def markdown_preview(text: str, limit: int = 200) -> str:
    """Collapse whitespace and truncate `text` to `limit` chars for a
    notification preview, without leaving a dangling markdown marker.

    A hard slice can cut between `**bold**`'s opener and closer; the leftover
    opener then renders as a literal `**`. We drop any now-unpaired emphasis /
    code markers and append an ellipsis so the preview reads cleanly. This only
    removes markers — it never rewrites the words (no semantics inferred).
    """
    collapsed = _WS.sub(" ", text).strip()
    if len(collapsed) <= limit:
        return collapsed
    cut = collapsed[:limit].rstrip()
    # Trailing marker directly at the cut, then any opener left unpaired inside.
    cut = re.sub(r"[*_`~]+$", "", cut).rstrip()
    for marker in _PAIRED:
        cut = _drop_unclosed(cut, marker)
    return f"{cut.rstrip()}…"
