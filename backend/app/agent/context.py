"""ActorContext: the identity + authorization context injected into every
tool call and every WS relay decision.

This is the ONE thing that must never come from agent-supplied JSON. Routes
resolve it from the session token (see ``sessions.py`` / ``router.py``) and
pass it down to services; tools receive it as an injected parameter (see
``registry.py``), never as part of their agent-facing schema.

Kept tiny and immutable (frozen dataclass) on purpose: extend by adding
fields WITH defaults so existing call sites keep constructing valid instances
(open-closed).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActorContext:
    """Who is calling, and what they are allowed to do.

    Attributes:
        actor_id: Stable identifier of the calling actor (e.g. the agent
            process, or a human user id for a takeover-driven call).
        scopes: Permission scopes held by this actor. Authorizer
            implementations decide what a scope means; the default
            ``PermissiveAuthorizer`` ignores this entirely.
        session_id: The connector session this actor is bound to.
        session_token: The raw token that authenticated this actor. Kept
            around for audit/logging; never re-validated against it directly
            downstream of the router (the router is the only place a token
            is trusted to resolve a session).
    """

    actor_id: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    session_id: str = ""
    session_token: str = ""

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes or "*" in self.scopes
