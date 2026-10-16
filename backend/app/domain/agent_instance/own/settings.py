"""The project settings that let members bring their own agents, and what they allow.

Off unless a manager turns it on. Three settings answer three questions — may a
member bring their own agent in at all, which kinds of connection may it use,
and which model-service hosts — and this module is the only place that reads
them. The body it runs on is the connection (`connections.Login`).
"""

from typing import Any

from app.core.sentences import listing as sentence_list
from app.core.sentences import say
from app.domain.agent_instance.own.connections import Login
from app.domain.device.login_kind import ConnectionKind

#: The project setting that lets members bring their own agent in. Off unless a
#: manager turns it on: the project's content leaves through a member's own
#: model account only where someone who manages the project chose that.
SETTING = "allow_own_agents"

#: The kinds of model connection (`ConnectionKind`) a project lets its members'
#: own agents use, as a list. Absent, every kind is allowed.
KINDS_SETTING = "own_agent_kinds"

#: The model services a project lets its members' own agents call, as a list
#: of hosts (the host of the service's address, ``api.moonshot.cn``). Absent or
#: empty, any service is allowed; it says nothing about the other kinds.
HOSTS_SETTING = "own_agent_service_hosts"

#: How a room names each kind (catalog ``roomNotice``).
_KIND_WORDS = {
    ConnectionKind.subscription: "ownAgentKindSubscription",
    ConnectionKind.api_key: "ownAgentKindApiKey",
    ConnectionKind.model_service: "ownAgentKindModelService",
}


def allows_own_agents(project_settings: dict[str, Any] | None) -> bool:
    return (project_settings or {}).get(SETTING) is True


def allowed_kinds(
    project_settings: dict[str, Any] | None,
) -> frozenset[ConnectionKind]:
    value = (project_settings or {}).get(KINDS_SETTING)
    if not isinstance(value, list):
        return frozenset(ConnectionKind)
    known = {kind.value for kind in ConnectionKind}
    return frozenset(ConnectionKind(v) for v in value if v in known)


def allowed_hosts(project_settings: dict[str, Any] | None) -> list[str]:
    """The model-service hosts a project allows, in the order its manager
    listed them; empty when it allows any."""
    value = (project_settings or {}).get(HOSTS_SETTING)
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str)]


def host_refused(login: Login, project_settings: dict[str, Any] | None) -> bool:
    """Whether the project refuses this connection's model service: it lists
    the hosts it allows, and this one is not among them. A service whose host
    is unknown is not shown to be allowed, so it is refused too."""
    hosts = allowed_hosts(project_settings)
    return (
        bool(hosts)
        and login.kind is ConnectionKind.model_service
        and login.service_host not in hosts
    )


def allows(login: Login, project_settings: dict[str, Any] | None) -> bool:
    """Whether the project lets its members' own agents use this connection:
    of a kind it allows, and, for a model service, at a host it allows."""
    return login.kind in allowed_kinds(project_settings) and not host_refused(
        login, project_settings
    )


def kinds_refused_sentence(owner_name: str, kinds: list[ConnectionKind]) -> str:
    """What the room is told when a member's own agent runs on a kind of
    connection the project does not allow."""
    return say(
        "ownAgentKindNotAllowed",
        owner=owner_name,
        kinds=sentence_list(say(_KIND_WORDS[kind]) for kind in kinds),
    )
