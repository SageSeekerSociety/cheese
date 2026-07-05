"""Business-function Protocols invoked by tools (``tools.py``).

Keeping these as Protocols separate from ``tools.py`` is the seam where real
cheese business logic (the actual chat/task/project domain) gets injected
later, via ``wiring.py`` -- tool definitions never change, only which
concrete service backs them.
"""

from dataclasses import dataclass
from typing import Protocol

from app.connector.context import ActorContext


@dataclass(frozen=True)
class ChatMessage:
    author_id: str
    text: str
    seq: int


class ChatService(Protocol):
    async def post_message(self, actor: ActorContext, message: str) -> ChatMessage: ...

    async def history(self, session_id: str) -> list[ChatMessage]: ...


class InMemoryChatService:
    """Default ``ChatService``: keeps messages in-process, keyed by
    ``actor.session_id``. Good enough for the demo app; a real deployment
    injects a service backed by the actual chat/discussion domain."""

    def __init__(self) -> None:
        self._messages: dict[str, list[ChatMessage]] = {}

    async def post_message(self, actor: ActorContext, message: str) -> ChatMessage:
        bucket = self._messages.setdefault(actor.session_id, [])
        entry = ChatMessage(author_id=actor.actor_id, text=message, seq=len(bucket))
        bucket.append(entry)
        return entry

    async def history(self, session_id: str) -> list[ChatMessage]:
        return list(self._messages.get(session_id, []))
