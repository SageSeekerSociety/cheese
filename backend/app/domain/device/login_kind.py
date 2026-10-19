"""Which kind of model connection a machine's owner login is (#2991).

A member's own Claude Code reaches a model one of three ways, and what it
costs, whose terms apply and where the project's content goes differ with each:
a Claude subscription, an Anthropic API key, or another model service its owner
pointed it at. A project may allow only some of them; this is the one place a
login row (`DeviceClaudeLoginRow`) is read as one of the three.
"""

import re
from enum import StrEnum

#: What a machine calls the login in its first login directory
#: (`place.CLAUDE_LOGIN_DIR`), the one it had before it could hold several.
#: Its record keeps the machine's id, which named it then.
FIRST_LOGIN = "default"

#: What a machine may call one of its logins: the name of its directory, which
#: the session's launch puts into a path. The connector's own copy is
#: `claudecode.loginID`; `tests/unit/test_footprint_root.py` holds the two equal.
LOGIN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

#: What a machine whose owner set another model service reports as its login
#: (`cheesehost claude login --base-url`).
MODEL_SERVICE = "model_service"

#: What `claude auth status` names a subscription login.
_SUBSCRIPTION_METHOD = "claude.ai"


class ConnectionKind(StrEnum):
    subscription = "subscription"
    api_key = "api_key"
    model_service = "model_service"


def connection_kind(
    auth_method: str | None, subscription_type: str | None
) -> ConnectionKind:
    """The kind of a logged-in machine's login. A Claude login that is not a
    subscription signs in with an API key."""
    if auth_method == MODEL_SERVICE:
        return ConnectionKind.model_service
    if auth_method == _SUBSCRIPTION_METHOD or subscription_type:
        return ConnectionKind.subscription
    return ConnectionKind.api_key
