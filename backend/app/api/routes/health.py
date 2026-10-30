"""The health routes. Every judgement they answer with is in ``app.core.health``.

Keeping the checks there and the routes here is what lets the platform
dashboard's panel ask the same question: it has no request and no credential,
so it reads ``health_report()`` in process, and a route module is not something
a domain service may import (`app/.importlinter`, C1).
"""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.admin_deps import PlatformAdminDep
from app.core.health import _REQUIRED_CHECKS, health_report
from app.core.metrics import registry

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness check")
async def health_check() -> dict[str, Any]:
    """Live means the process is up and answering — nothing more.

    Whether it should take traffic is `/readyz`'s question, and every gate that
    decides that (the rollout, the container healthcheck) reads `/readyz`. This
    one stays 200 through a dependency outage or an unmounted route module: a
    restart fixes neither, so a liveness probe that failed on them would only
    add a restart loop to the outage.
    """
    return {"status": "ok"}


@router.get("/health/detailed", summary="Detailed health check")
async def detailed_health_check(_admin: PlatformAdminDep) -> dict[str, Any]:
    """Per-check detail, for a platform admin.

    Gated because it names the platform's dependencies and how each one is
    failing. The probes monitoring reads stay public (`/healthz`, `/readyz`,
    `/health`); while something required is down, `/readyz`'s 503 names which
    checks failed and each one's status, so an outage stays diagnosable with no
    session — the error text itself is only here.
    """
    return await health_report()


@router.get("/metrics", summary="Application metrics")
async def get_metrics(_admin: PlatformAdminDep) -> dict[str, Any]:
    """The in-process metric registry, for a platform admin.

    Every route's call count, latency and error count lives in here, which is
    a map of the platform's insides — not something the open internet reads.
    No scraper is configured to read it today, so nothing external breaks when
    it goes behind the gate.
    """

    return registry.export()


@router.get("/readyz", summary="Readiness check")
async def readiness_check() -> Any:
    """Ready means "can serve requests", which is narrower than "all green".

    Only `_REQUIRED_CHECKS` can make this 503. A degraded advisory check still
    shows up in `/health/detailed` — that is where a human or an alert looks —
    but taking the process out of rotation over it would trade one degraded
    feature for a total outage.

    The 503 is a plain JSON response, not an `HTTPException`: the app's error
    envelope would replace the body with a generic "HTTP error". The body names
    which required checks failed and each check's status, plus the modules that
    did not mount — enough for a rollout log to say why. The error text of a
    failing dependency stays behind `/health/detailed`: this route is public,
    and that text can carry internal hosts and users.
    """
    result = await health_report(domain=False)
    checks = result["checks"]
    unready = [
        name for name in _REQUIRED_CHECKS if checks.get(name, {}).get("status") != "up"
    ]
    if unready:
        public = {name: {"status": body.get("status")} for name, body in checks.items()}
        if "unmounted" in checks.get("routes", {}):
            public["routes"]["unmounted"] = checks["routes"]["unmounted"]
        return JSONResponse(
            status_code=503,
            content={"status": "unready", "unready": unready, "checks": public},
        )
    return {"status": "ready"}
