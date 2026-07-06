"""Factory constructing the default connector object graph.

This is the ONLY place that decides which concrete class backs each
Protocol seam (``SessionStore``, ``Authorizer``, ``ChatService``, ...).
Swap an implementation here -- e.g. a Redis-backed ``SessionStore``, a
scope-checking ``Authorizer``, a real ``ChatService`` wired to the actual
discussion domain -- without touching ``router.py``, ``tools.py``, or
``proxy.py``.
"""

from dataclasses import dataclass

from app.agent.authorization import Authorizer, PermissiveAuthorizer
from app.agent.proxy import SessionHub
from app.agent.registry import ToolRegistry
from app.agent.services import ChatService, InMemoryChatService
from app.agent.sessions import InMemorySessionStore, SessionStore
from app.agent.tools import register_default_tools


@dataclass
class ConnectorGraph:
    """The wired object graph ``router.py`` binds to HTTP/WS endpoints."""

    sessions: SessionStore
    authorizer: Authorizer
    chat_service: ChatService
    registry: ToolRegistry
    hub: SessionHub


def build_default_graph(
    *,
    sessions: SessionStore | None = None,
    authorizer: Authorizer | None = None,
    chat_service: ChatService | None = None,
) -> ConnectorGraph:
    """Construct a fully-wired ``ConnectorGraph``. Every dependency can be
    overridden individually (e.g. tests inject a fake ``SessionStore``)
    while everything else keeps its in-memory default."""
    sessions = sessions if sessions is not None else InMemorySessionStore()
    authorizer = authorizer if authorizer is not None else PermissiveAuthorizer()
    chat_service = chat_service if chat_service is not None else InMemoryChatService()

    registry = ToolRegistry()
    register_default_tools(registry, chat_service)

    return ConnectorGraph(
        sessions=sessions,
        authorizer=authorizer,
        chat_service=chat_service,
        registry=registry,
        hub=SessionHub(sessions),
    )
