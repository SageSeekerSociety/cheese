"""Print a digest of the Python code a service runs: `code-digest <hex>`.

deploy-docker.sh feeds this file on stdin to `python - <module>…`, once in the
running service and once in the release's image, and recreates the service
only when the two differ. The service here is the forge event relay, which runs
the backend image: that image changes with nearly every backend commit, while
the relay's own code (app.forge_events_app and what it imports) rarely does,
and recreating the relay drops every deployment's event socket.

The digest covers every module the named modules import, the standard library
and installed packages included; what uvicorn loads only once it serves (its
event loop, uvloop among them, and its HTTP and WebSocket implementations); the
interpreter's version; and the operating system's installed packages
(/var/lib/dpkg/status), so a security update in the base image moves it too.
A module the service would import later, from inside a function, is not
covered, so this suits a service whose imports are all at the top of its
files; deploy/tests/test_code_digest.py checks that the relay's are.
"""

import hashlib
import importlib
import sys

for name in sys.argv[1:]:
    importlib.import_module(name)
for name in (
    "uvloop",
    "httptools",
    "websockets",
    "wsproto",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
):
    try:
        importlib.import_module(name)
    except ImportError:
        pass

digest = hashlib.sha256(sys.version.encode())
for name, module in sorted(sys.modules.items()):
    path = getattr(module, "__file__", None)
    if not path or name == "__main__":
        continue
    try:
        with open(path, "rb") as handle:
            content = handle.read()
    except OSError:
        continue
    digest.update(name.encode() + b"\0" + content + b"\0")
for path in ("/etc/os-release", "/var/lib/dpkg/status"):
    try:
        with open(path, "rb") as handle:
            digest.update(path.encode() + b"\0" + handle.read() + b"\0")
    except OSError:
        pass
print(f"code-digest {digest.hexdigest()}")
