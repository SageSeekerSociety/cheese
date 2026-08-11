"""Friendly-@ canonicalization, shared by every platform write path.

Chat replies have long had this backstop (chat.py): a friendly "@名字 /
@handle / @话题名" is deterministically rewritten into the structured token
(<@handle> / <#id>) so it renders as a clickable chip and notifies. Docs,
decisions, and returned conclusions render through the same token-aware
pipelines but used to skip the rewrite — a doc saying "@张衡" stayed plain
text. This module hosts the shared rewrite so ALL those write paths (PUT doc,
decision, return-conclusion, chat reply) canonicalize identically.

Notification title/body are rendered as plain text in the UI (no token
decoration), so they are deliberately NOT canonicalized — a raw token there
would look worse than the friendly name.
"""

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.repositories import ProjectRepository
from app.domain.topic.models import TopicKind
from app.domain.topic.repositories import TopicRepository

# After an ASCII-word-ending @name/@handle, the next char must not continue the
# word — so roster handle "andy" never eats the front of a literal "@andyl".
# ASCII-only on purpose: Python's \w matches CJK, and "@张衡来负责" must still
# resolve 张衡 even though 来 follows without a space.
_ASCII_WORD = re.compile(r"[A-Za-z0-9_-]$")
_ASCII_BOUNDARY = r"(?![A-Za-z0-9_-])"


def expand_mention_names(
    text: str, roster: list[dict], topics: list[dict] | None = None
) -> str:
    """Canonicalize a friendly "@名字 / @handle / @话题名" into the structured
    token (<@handle> / <#id>) — deterministic exact-match against the
    roster/topics, longest first. Both the display name AND the handle work:
    in chat people are labeled by handle, so "@andyl" must resolve even when
    andyl's display name differs. Matching is case-insensitive so "@Alice"
    (display name "Alice", or the handle typed in the wrong case) still resolves
    to the canonical lowercase handle token <@alice>. Tokens already present are
    untouched (they don't match the @name patterns)."""
    subs: list[tuple[str, str]] = []
    for m in roster:
        tok = f"<@{m['handle']}>"
        for key in (m.get("name"), m.get("handle")):
            if key:  # an empty pattern ("@") would swallow every @ in the text
                subs.append((f"@{key}", tok))
    subs += [(f"@{t['title']}", f"<#{t['id']}>") for t in (topics or []) if t["title"]]
    subs.sort(key=lambda s: len(s[0]), reverse=True)
    seen: set[str] = set()
    for pat, tok in subs:
        if pat in seen:  # name == handle yields the same pattern twice
            continue
        seen.add(pat)
        boundary = _ASCII_BOUNDARY if _ASCII_WORD.search(pat) else ""
        # (?<!<) keeps already-encoded tokens intact: the "@handle" inside a
        # produced "<@handle>" must not be re-wrapped by a later pattern.
        # bind tok per-iteration (B023): a bare closure would see the last tok.
        # IGNORECASE: "@Alice" (name "Alice") and a mis-cased handle both map to
        # the canonical lowercase token; the replacement tok is always the
        # stored lowercase handle, so the emitted token is exactly <@alice>.
        text = re.sub(
            r"(?<!<)" + re.escape(pat) + boundary,
            lambda _m, t=tok: t,
            text,
            flags=re.IGNORECASE,
        )
    return text


async def canonicalize_refs(
    session: AsyncSession,
    project_id: uuid.UUID,
    text: str,
    *,
    exclude_topic_id: uuid.UUID | None = None,
) -> str:
    """Load the project's roster + topic list and canonicalize friendly @refs
    in `text`. The cheap no-@ case never touches the DB."""
    if not text or "@" not in text:
        return text
    roster = await ProjectRepository(session).list_members(project_id)
    topics = [
        {"id": str(t.id), "title": t.title}
        for t in await TopicRepository(session).list_for_project(project_id)
        if t.kind != TopicKind.root and t.id != exclude_topic_id
    ]
    return expand_mention_names(text, roster, topics)
