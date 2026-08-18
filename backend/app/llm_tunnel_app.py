"""The llm tunnel as its OWN process — #551's terminal state.

The tunnel WebSocket used to terminate inside the backend process, so every
deploy that swapped the backend container severed all machine model traffic
for the swap window. This module is the same route (`app.api.routes.
llm_tunnel`) mounted alone in a minimal ASGI app, so a standing container can
own the data plane while the backend restarts freely underneath:

    uvicorn app.llm_tunnel_app:app --host 0.0.0.0 --port 8091

It rides the ordinary backend image (same code, different command — see
deploy/llm-tunnel/), and deliberately imports nothing of app.main: the route
authenticates by HMAC over the scoped token's own claims and never touches
the database, which is exactly what makes it safe to run detached from the
rest of the app.
"""

from fastapi import FastAPI

from app.api.routes.llm_tunnel import router

app = FastAPI(
    title="cheese-llm-tunnel",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "service": "llm-tunnel"}
