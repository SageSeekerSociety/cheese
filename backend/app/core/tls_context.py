"""One TLS context for every outbound HTTPS client.

`httpx.AsyncClient()` builds an `ssl.SSLContext` and loads the trust store into
it. Measured here that is about 12 ms for the context and the rest for
`certifi`'s bundle, and it happens on the thread that asks for the client — in
this deployment the event loop's, because clients are built inside request
handlers and background jobs. `forge_client` alone is constructed at 41 call
sites, several of them per request.

The context does not vary per call: same interpreter, same trust store, same
flags. Building it once and handing the same object to every client is what
`verify=` is for, and httpx passes an `ssl.SSLContext` straight through to the
pool (`httpx._config.create_ssl_context` returns it unchanged), so every
connection on that client reuses this one. Measured on this machine: a client
that builds its own context, 36 ms; one handed this, under 1 ms.

It is built at import, in the process's start-up, before the loop is serving;
reading the attribute afterwards is a module lookup.
"""

from __future__ import annotations

import ssl

import certifi

#: The context every outbound HTTPS client here verifies against: the same
#: trust store and the same checks `httpx.AsyncClient()` builds by default.
#:
#: Mutated by httpcore, not by us: `httpcore._async.connection` calls
#: `set_alpn_protocols` on whichever context it is given, to the value its own
#: connection wants ("http/1.1", or that plus "h2" when HTTP/2 is asked for).
#: Sharing one context therefore means two clients with different HTTP/2
#: settings would set it for each other. No client here asks for HTTP/2
#: (`httpx.AsyncClient(http2=...)` is not used), so the value is the same one
#: every time; a caller that ever does ask would need its own context.
OUTBOUND_TLS = ssl.create_default_context(cafile=certifi.where())
