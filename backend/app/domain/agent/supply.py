"""Which pool serves a project's model traffic (issue #243).

The metering proxy is the one interception point every sandbox's traffic passes
through. It has to decide, per request, where that request actually goes:

  subscription  Anthropic, on the session's own Claude credential
  gateway       an API-key pool (Zhipu / DeepSeek), reached through LiteLLM

The backend chooses the route at admission, from the model binding this request
runs under (`room_task/binding.py`): a project supply setting picks the default
model, and the model picks the pool that serves it. Admission also supplies the
central account identity for profile queries, independently of inference supply.

Kept pure (no DB, no HTTP) so both the endpoint and its tests can call it with a
plain settings dict.
"""

from dataclasses import dataclass

SUBSCRIPTION = "subscription"
GATEWAY = "gateway"

# An unset project uses the configured gateway model. Launch credentials do
# not choose the inference provider; admission routes the selected model.
SUPPLY_KEY = "supply"

_VALID = frozenset({SUBSCRIPTION, GATEWAY})


@dataclass(frozen=True)
class Supply:
    """Where one project's traffic goes, and what it authenticates with.

    ``key`` is only meaningful for ``gateway`` — the project's virtual LiteLLM
    key. The subscription needs nothing from here: the session's request already
    carries its host's Claude credential.
    """

    pool: str
    key: str | None = None


def resolve_pool(settings: dict | None) -> str:
    """The pool a project runs on.

    An explicit ``settings["supply"]`` wins; anything unrecognised falls back to
    the gateway rather than failing the turn — a typo in a settings blob
    must not take a project offline.

    Both pools remain available. The subscription-shaped launch credentials
    bootstrap the client; they must not override the configured default model.
    """
    chosen = (settings or {}).get(SUPPLY_KEY)
    if isinstance(chosen, str) and chosen in _VALID:
        return chosen
    return GATEWAY
