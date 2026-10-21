"""Kinds of model credential: computer-held subscriptions and platform-held keys."""

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


class ConnectionKind(StrEnum):
    subscription = "subscription"
    api_key = "api_key"
    model_service = "model_service"
