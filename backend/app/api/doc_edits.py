"""Who a change to part of the living document is for, and whether it is made
or only proposed.

The room's agent changes the document when someone asks it to, and the change
is recorded as theirs to answer for: 「李老师让芝士改了文档」. Nobody asked when
its turn was started by the platform (a task finished, the document-upkeep
nudge); then rewriting what a person wrote is proposed as a suggestion that a
person accepts or rejects. Adding new text, or changing text the agent itself
wrote, needs nobody's consent and stays a direct edit even then — otherwise
every upkeep pass would bury the room in suggestions. The agent may also choose
to suggest on its own.

A person calling here (restoring one change, undoing a rewrite) edits directly,
as themselves.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.repositories import AgentTurnRepository
from app.domain.identity.services import IdentityService
from app.domain.living_doc.models import Document
from app.domain.living_doc.services import Documents


@dataclass(frozen=True)
class Decision:
    mode: str
    requested_by: str | None


def changed_span(old: str, new: str) -> tuple[int, int]:
    """The part of ``old`` an edit actually replaces: ``old`` without what it
    shares with ``new`` at either end. Empty when the edit only inserts."""
    start = 0
    while start < min(len(old), len(new)) and old[start] == new[start]:
        start += 1
    end = 0
    while (
        end < min(len(old), len(new)) - start
        and old[len(old) - 1 - end] == new[len(new) - 1 - end]
    ):
        end += 1
    return start, len(old) - end


async def _touches_someone_elses_text(
    db: AsyncSession, doc: Document, content: str, edits: list[dict]
) -> bool:
    """Whether any edit replaces text a person wrote.

    Who wrote what is the document's node tree: each top-level block is
    attributed to whoever last changed it. An edit whose passage cannot be
    placed is left to the collaboration service to refuse; here it counts as
    touching a person's text, so it can never slip through as direct.
    """
    # Where each node's text sits in the stored Markdown, in document order.
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for node in await Documents(db).nodes(doc):
        at = content.find(node.content, cursor)
        if at < 0:
            continue
        spans.append((at, at + len(node.content), node.author))
        cursor = at + len(node.content)
    agents = await IdentityService(db).agents_among(sorted({a for _, _, a in spans}))
    for edit in edits:
        start, end = changed_span(edit["old"], edit["new"])
        if start == end:
            continue
        at = content.find(edit["old"])
        if at < 0 or content.find(edit["old"], at + 1) >= 0:
            return True
        lo, hi = at + start, at + end
        if any(s < hi and lo < e and author not in agents for s, e, author in spans):
            return True
    return False


async def decide(
    db: AsyncSession,
    *,
    doc: Document,
    actor: str,
    content: str,
    edits: list[dict],
    asked: str | None,
) -> Decision:
    """How ``actor``'s edits are applied to ``doc``. ``asked`` is the mode the
    caller asked for, if any; ``content`` the document as stored. An agent
    editing a room's document in a turn somebody started edits for them."""
    if not await IdentityService(db).is_agent(actor):
        return Decision(mode="direct", requested_by=None)
    author = (
        await AgentTurnRepository(db).open_turn_author_for_topic(
            doc.room_id, agent_handle=actor
        )
        if doc.room_id is not None
        else None
    )
    if author and author != "system" and not await IdentityService(db).is_agent(author):
        return Decision(mode=asked or "direct", requested_by=author)
    if asked == "suggest" or await _touches_someone_elses_text(db, doc, content, edits):
        return Decision(mode="suggest", requested_by=None)
    return Decision(mode="direct", requested_by=None)
