"""Which part of the backend this process plays, and what only one part may do.

A container runs one `realtime` process and, when `API_POOL_WORKERS` asks for
it, a pool of `api` processes beside it (`app/serve.py`). The realtime process
is the backend as it always was: it holds the owner lock (`core/ownership`),
starts and reads turns, keeps the room sockets and the broker that feeds them.
The pool only answers plain reads — every `GET` except the ones below — which
is where the CPU of a busy page goes: one Python process runs one core, so a
channel list being built held up every other request behind it.

app-router sends a request to the pool by the same rule (`realtime_routes.conf`,
generated from `REALTIME_GET_PATTERNS` and pinned by
`tests/unit/test_process_role_routes.py`). Whatever reaches a pool process
anyway and touches the realtime process's state fails loudly
(`ProcessRoleError`) instead of answering from a state that is not there.
"""

import re

from app.core.config import settings


class ProcessRoleError(RuntimeError):
    """A pool process was asked for something only the realtime process has."""


def is_api_pool() -> bool:
    return settings.backend_role == "api"


def refuse_on_api_pool(what: str) -> None:
    """Fail where a pool process would otherwise do ``what`` without the state
    it needs (or wait for an owner lock it never takes)."""
    if is_api_pool():
        # i18n-exempt: a request routed to the wrong process, a 500 no reader is told
        raise ProcessRoleError(f"an api pool process cannot {what}")


#: `GET` routes that read the realtime process's own state, so app-router keeps
#: them on it. Paths as the backend sees them (app-router has already removed
#: `/api`), anchored, one route family each. Every other `GET` that is not a
#: WebSocket upgrade may go to the pool.
REALTIME_GET_PATTERNS: tuple[str, ...] = (
    # Health, metrics and the admin dashboards describe the process that
    # answers them (its jobs, its ownership, its counters); the deploy's probes
    # through app-router and the dashboards mean the realtime one.
    r"^/(health|healthz|readyz)(/|$)",
    r"^/metrics",
    r"^/admin/",
    r"^/debug/",
    r"^/_internal/",
    r"^/internal/",
    # A topic's running turn: the runner's ring buffer and the broker's clocks.
    r"^/topics/[^/]+/status$",
    r"^/topics/[^/]+/agent/control$",
    r"^/run-records/turns",
    # Sessions this process reads (`SessionHost`), who is reachable, and the
    # answers a session is still writing (`Consumptions`).
    r"^/topics/[^/]+/session-transcript",
    r"^/assistant/conversations/",
    r"^/documents/[^/]+/agent/",
    # Previews held or woken in process (legacy preview mode).
    r"^/topics/[^/]+/preview",
    # Machines and models proxied through the realtime process.
    r"^/machine/",
    r"^/llm/",
    r"^/connector/",
    r"^/sandbox/",
    r"^/preview/",
)

_REALTIME_GET = re.compile("|".join(f"(?:{p})" for p in REALTIME_GET_PATTERNS))


def served_by_pool(method: str, path: str, *, upgrade: bool = False) -> bool:
    """Whether app-router may send this request to the api pool."""
    if upgrade or method.upper() not in {"GET", "HEAD"}:
        return False
    return _REALTIME_GET.search(path) is None
