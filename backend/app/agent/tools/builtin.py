"""The built-in agent tools, backed by real business services.

Every tool derives ``project_id`` from the injected ``ProjectActor`` — never
from agent-supplied arguments — so an agent can only ever act inside its own
project (the aggregate-root boundary, enforced structurally at the tool layer).
The chat plane stays thin (one short message); substance goes to the document
tree (architecture doc §5).
"""

from app.agent.authorization.authorizer import ProjectActor
from app.agent.tools.context import ToolContext
from app.agent.tools.registry import ToolRegistry
from app.core.errors import ForbiddenError
from app.domain.block.models import AuthorKind, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.services import BlockService
from app.domain.thread.repositories import ThreadRepository
from app.domain.thread.services import ThreadService

builtin_registry = ToolRegistry(injected_types=(ProjectActor, ToolContext))


def _author_kind(actor: ProjectActor) -> AuthorKind:
    return AuthorKind.AGENT if actor.kind == "agent" else AuthorKind.USER


def _block_service(ctx: ToolContext) -> BlockService:
    return BlockService(BlockRepository(ctx.session))


async def post_message(
    ctx: ToolContext, actor: ProjectActor, thread_id: int, content: str
) -> dict[str, int]:
    """Post one short message to a thread the actor's project owns. Keep it to a
    sentence; put substance in a document and reference it."""
    threads = ThreadService(ThreadRepository(ctx.session))
    thread = await threads.get_thread(thread_id)
    if thread.project_id != actor.project_id:
        raise ForbiddenError("thread belongs to another project")
    block = await _block_service(ctx).create_block(
        project_id=actor.project_id,
        content=content,
        author_id=actor.actor_id,
        author_kind=_author_kind(actor),
        kind=BlockKind.MESSAGE,
        thread_id=thread_id,
    )
    return {"block_id": block.id}


async def write_document(
    ctx: ToolContext, actor: ProjectActor, content: str, struct_parent_id: int | None = None
) -> dict[str, int]:
    """Write a document block into the project's document tree, optionally under
    a parent block. Use this for substance rather than long chat messages."""
    block = await _block_service(ctx).create_block(
        project_id=actor.project_id,
        content=content,
        author_id=actor.actor_id,
        author_kind=_author_kind(actor),
        kind=BlockKind.DOCUMENT,
        struct_parent_id=struct_parent_id,
    )
    return {"block_id": block.id}


builtin_registry.register(post_message)
builtin_registry.register(write_document)
