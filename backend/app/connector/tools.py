"""Concrete tools, registered onto a ``ToolRegistry`` via ``@tool``.

Each tool is a plain, annotated async function. ``registry.py`` projects its
schema from the signature/docstring; ``router.py`` authorizes the call (via
``authorization.Authorizer``) and then dispatches it through
``ToolRegistry.invoke``, which injects ``ActorContext`` -- tools never
receive it as an agent-supplied argument, and never see agent JSON that
wasn't declared in their signature.

Registration is a plain function (``register_default_tools``), not
import-time global state, so independent object graphs (one per test, one
per demo run) never share tool registrations -- see ``wiring.py``.
"""

from app.connector.context import ActorContext
from app.connector.registry import ToolRegistry, tool
from app.connector.services import ChatService


def register_default_tools(registry: ToolRegistry, chat_service: ChatService) -> None:
    """Register the built-in tool set onto ``registry``."""

    @tool(
        name="chat",
        description="Post a message from the agent into the session's chat.",
        registry=registry,
    )
    async def chat(actor: ActorContext, message: str) -> dict[str, object]:
        """Post ``message`` into the calling session's chat, as the agent."""
        posted = await chat_service.post_message(actor, message)
        return {"authorId": posted.author_id, "text": posted.text, "seq": posted.seq}
