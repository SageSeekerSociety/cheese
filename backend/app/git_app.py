"""A process whose only job is serving each project's repo over git's protocol.

Serving a repo is a different kind of work from answering the platform's API:
`git http-backend` computes and streams a pack, which for one project on dev
was 169 MB and 3.7 seconds. Sharing a process with every other request means
one of those competes with all of them — for memory, for worker threads, and
for the release that restarts them. It held the event loop outright until
#1270, and the alert that reached the room said `Timeout reading from
…:6379`, because a Redis read was what noticed first.

So git gets its own process, from the same image, mounting only these routes.
The front nginx sends `/api/projects/<id>/git/…` here and everything else to
the backend, which is where a per-route rate and size limit belongs too — the
platform has no other place today where a machine can ask for an unbounded
number of bytes.

Authorization does not move: `git_http._repo_for` verifies the same
project-scoped token it always did, in this process, before a byte is served.
"""

import logging

from fastapi import FastAPI

from app.api.routes.git_http import router as git_router
from app.core.errors import register_exception_handlers
from app.core.obs import configure_logging

configure_logging()

logger = logging.getLogger(__name__)

app = FastAPI(title="Cheese git", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(git_router)
register_exception_handlers(app)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
