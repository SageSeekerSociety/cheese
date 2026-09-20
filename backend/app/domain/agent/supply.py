"""Which pool serves a project's model traffic (issue #243).

The metering proxy is the one interception point every sandbox's traffic passes
through. It has to decide, per request, where that request actually goes:

  subscription  the Claude subscription, reached through ccproxy's egress
  gateway       an API-key pool (Zhipu / DeepSeek), reached through LiteLLM

The backend chooses the route from the agent model signed into its session
credential. A project supply setting chooses the default model and remains the
route for older credentials without a model claim. Admission also supplies the
central account identity for profile queries, independently of inference supply.

Kept pure (no DB, no HTTP) so both the endpoint and its tests can call it with a
plain settings dict.
"""

from dataclasses import dataclass

SUBSCRIPTION = "subscription"
GATEWAY = "gateway"

# project.settings key. Absent = follow the deployment default, which keeps
# every existing project on exactly the supply it has today.
SUPPLY_KEY = "supply"

_VALID = frozenset({SUBSCRIPTION, GATEWAY})


@dataclass(frozen=True)
class Supply:
    """Where one project's traffic goes, and what it authenticates with.

    ``key`` is only meaningful for ``gateway`` — the project's virtual LiteLLM
    key. The subscription's real credential is never sent here: it lives on the
    proxy and only the proxy ever holds it (a credential that reaches the
    control plane's responses is a credential in one more place than it needs
    to be).
    """

    pool: str
    key: str | None = None


def resolve_pool(settings: dict | None, *, subscription_enabled: bool) -> str:
    """The pool a project runs on.

    An explicit ``settings["supply"]`` wins; anything unrecognised falls back to
    the deployment default rather than failing the turn — a typo in a settings
    blob must not take a project offline, and the default is always a pool that
    works on this deployment.
    """
    chosen = (settings or {}).get(SUPPLY_KEY)
    if isinstance(chosen, str) and chosen in _VALID:
        return chosen
    return SUBSCRIPTION if subscription_enabled else GATEWAY
