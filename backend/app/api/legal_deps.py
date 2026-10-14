"""What the routes around consent records share.

`client_context` reads the (address, user agent) pair a consent row records.
Three route modules ask for it — `routes/legal.py`, and the OAuth sign-up and
the account registration in `routes/users/` — and all three write through the
same `ConsentService`, so it belongs to none of them: a route module does not
import another one (C4 `routes-independent`), and a fourth caller would
otherwise mean a fourth copy or a fourth edge.

No router here and nothing to mount: the name below is imported by the modules
that do mount one.
"""

from fastapi import Request


def client_context(request: Request) -> tuple[str, str]:
    """(ip, user agent) as a consent row records them. The address is the one
    the server resolved through the proxies it trusts (FORWARDED_ALLOW_IPS),
    never X-Forwarded-For as sent: its left part is whatever the client wrote."""
    ip = request.client.host if request.client else ""
    return ip, request.headers.get("user-agent", "")
