"""Reset the demo projects' charter (章程) docs to a clean empty state.

Earlier agent testing autosaved junk (e.g. "[auto-1783791414305]") into some
demo projects' living docs. This walks every project's root-topic living doc
(the ``kind='doc'`` block, spec §2.2) and, if its content is empty OR looks like
leftover autosave test junk, resets it to an empty doc — clearing the markdown
blob and deleting the mirrored ``doc_node`` tree so the editor shows a clean
placeholder (matching root topics that never had a doc).

Operates directly on the Block model (no TopicService.edit_doc) so it does NOT
append a "编辑了文档" event to the conversation timeline — this is a silent
data cleanup, not a user edit.

Idempotent: a doc that is already empty (no content, no doc_nodes) is skipped,
so re-running is a no-op. Non-junk docs are left untouched.

Run:  cd backend && PYTHONPATH=. .venv/bin/python scripts/clean_demo_charter_docs.py
"""

import asyncio
import re

from sqlalchemy import select

import app.models  # noqa: F401 — register every model so cross-table FKs resolve
from app.core.db import async_session_factory
from app.domain.block.models import Block, BlockKind
from app.domain.project.models import Project

# Autosave test junk written by earlier agent runs, e.g. "[auto-1783791414305]"
# (stored markdown escapes the brackets as "\[auto-...\]", so match on the
# stable "auto-<timestamp>" marker rather than the brackets).
_JUNK_RE = re.compile(r"auto-\d{10,}")


def _looks_like_junk(content: str) -> bool:
    """True if the doc's markdown is empty or is leftover autosave test junk.

    "Empty" (whitespace / stray ``&nbsp;`` placeholders only) or containing an
    ``[auto-<ts>]`` marker → junk. Anything with real prose is left alone.
    """
    stripped = content.replace("&nbsp;", "").strip()
    if not stripped:
        return True
    return bool(_JUNK_RE.search(content))


async def clean() -> None:
    async with async_session_factory() as s:
        projects = list((await s.execute(select(Project))).scalars().all())
        for p in projects:
            if p.root_topic_id is None:
                print(f"skip {p.name!r}: no root_topic_id")
                continue
            doc = (
                (
                    await s.execute(
                        select(Block)
                        .where(
                            Block.topic_id == p.root_topic_id,
                            Block.kind == BlockKind.doc,
                        )
                        .order_by(Block.created_at)
                    )
                )
                .scalars()
                .first()
            )
            if doc is None:
                print(f"skip {p.name!r}: no charter doc (already clean/empty)")
                continue

            nodes = list(
                (
                    await s.execute(
                        select(Block).where(
                            Block.topic_id == p.root_topic_id,
                            Block.kind == BlockKind.doc_node,
                        )
                    )
                )
                .scalars()
                .all()
            )

            already_empty = not doc.content.strip() and not nodes
            if already_empty:
                print(f"skip {p.name!r}: doc already empty")
                continue

            if not _looks_like_junk(doc.content):
                print(f"keep {p.name!r}: doc has real content, not junk")
                continue

            # Reset to a clean empty doc: clear the markdown blob and drop the
            # mirrored doc_node tree.
            doc.content = ""
            for n in nodes:
                await s.delete(n)
            print(
                f"clean {p.name!r}: reset doc {doc.id} "
                f"(cleared content + deleted {len(nodes)} doc_node(s))"
            )

        await s.commit()


if __name__ == "__main__":
    asyncio.run(clean())
