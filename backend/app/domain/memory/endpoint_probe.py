"""Does the memory backend's model endpoint answer — with the key it was given?

The openviking backend fails silently by construction. Extraction is handed to
a background task inside OpenViking, the read path returns empty on any
exception, and a rejected key is at most a log line nobody is reading; the
platform then behaves exactly like the db backend, which also never grows
memories on its own. So "we turned it on" and "it is working" look identical
from the outside, and the one piece of evidence that separates them — a single
call to each endpoint — is never made by anything on the normal path.

This module makes that call. It talks to the same two URLs, with the same key
and model, that :func:`openviking_store.model_endpoints` writes into ov.conf,
so a green probe means the extractor's own calls would be accepted too.

Two callers, two different jobs:

- boot (``app.main`` lifespan) — whoever just flipped ``MEMORY_BACKEND`` is
  watching the container log at that exact moment, which is the one moment a
  bad key is cheap to fix.
- ``/health/detailed`` — the durable place to look afterwards, and the only
  way a key that stops working *later* is ever noticed.

It is deliberately NOT wired into ``/healthz``. That endpoint is the container
health check, and therefore the deploy's rollback gate: ``deploy-docker.sh``
waits on ``check-app-tier.sh``, which reads ``(healthy)`` off ``docker ps``.
A memory endpoint being down costs one feature; rolling a whole release back
because the model vendor is having a bad afternoon costs more than the failure
this module exists to surface. Reporting loudly and gating are separable, and
this only does the first.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from app.core.config import settings

if TYPE_CHECKING:  # the openviking module tree stays unimported at runtime
    from app.domain.memory.openviking_store import ModelEndpoint

logger = logging.getLogger(__name__)

# Long enough that a slow-but-working endpoint is not called broken, short
# enough that boot is not held hostage by one that will never answer. Both
# endpoints are probed concurrently, so this is the whole probe's ceiling.
PROBE_TIMEOUT_S = 10.0

# How long a verdict stays fresh. The failure being watched for is a
# configuration one, which does not come and go — so this is about catching the
# *later* kind (a rotated or expired key), not about sampling densely.
PROBE_TTL_S = 300.0

_PROBE_INPUT = "cheesex memory backend probe"

# Response bodies are quoted back so the operator gets the vendor's own words
# ("invalid api key" and "model not found" have different fixes), but a body is
# unbounded remote input on its way to a log line: one line, hard cap.
_ERROR_BODY_CHARS = 200


@dataclass(frozen=True)
class EndpointStatus:
    """One endpoint's verdict. An empty ``error`` means it answered correctly."""

    role: str
    url: str
    model: str
    key_setting: str
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "up" if self.ok else "down",
            "url": self.url,
            "model": self.model,
            "key_from": self.key_setting,
        }
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True)
class ProbeResult:
    endpoints: tuple[EndpointStatus, ...]
    checked_at: str

    @property
    def ok(self) -> bool:
        return all(endpoint.ok for endpoint in self.endpoints)

    def summary(self) -> str:
        """Every failure, each carrying where it was and which key it used.

        "401" alone sends the reader back to the settings file to guess. The
        key's *source* is the answer to the first question they would ask, and
        it is not recoverable from anywhere else.
        """
        return "; ".join(
            f"{e.role} ({e.url}, key from {e.key_setting}): {e.error}"
            for e in self.endpoints
            if e.error
        )

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "up" if self.ok else "down",
            "backend": "openviking",
            "checked_at": self.checked_at,
            "endpoints": {e.role: e.as_dict() for e in self.endpoints},
        }
        if not self.ok:
            payload["error"] = self.summary()
        return payload


def _headers(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _http_error(response: httpx.Response) -> str:
    if response.status_code < 400:
        return ""
    body = " ".join(response.text.split())[:_ERROR_BODY_CHARS]
    return f"HTTP {response.status_code}{': ' + body if body else ''}"


async def _probe_embedding(client: httpx.AsyncClient, endpoint: "ModelEndpoint") -> str:
    """Ask for one vector. Returns "" if the answer is usable, else why not."""
    body: dict[str, Any] = {"model": endpoint.model, "input": _PROBE_INPUT}
    if endpoint.encoding_format:
        body["encoding_format"] = endpoint.encoding_format
    response = await client.post(
        f"{endpoint.api_base.rstrip('/')}/embeddings",
        json=body,
        headers=_headers(endpoint.api_key),
    )
    failed = _http_error(response)
    if failed:
        return failed
    try:
        vector = response.json()["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return f"answered 200, but not with an embedding ({exc!r})"
    if not vector:
        return "answered 200 with an empty embedding"
    # ov.conf declares "provider": "openai", and OpenViking's OpenAI embedder
    # does not send `dimensions` for that provider — so the width of the vectors
    # is whatever the model natively returns, while the index is built to
    # openviking_embedding_dimension. The two disagreeing is a *working* key
    # that still yields a broken store, which is exactly the shape of failure
    # this probe exists to refuse to let past.
    if endpoint.dimension and len(vector) != endpoint.dimension:
        return (
            f"returned {len(vector)}-dimensional vectors while "
            f"openviking_embedding_dimension is {endpoint.dimension} — "
            "the vector index would be built at the wrong width"
        )
    return ""


async def _probe_chat(client: httpx.AsyncClient, endpoint: "ModelEndpoint") -> str:
    """Ask for one completion — extraction spends one of these per fact kept."""
    body: dict[str, Any] = {
        "model": endpoint.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 16,
        **endpoint.extra_body,
    }
    response = await client.post(
        f"{endpoint.api_base.rstrip('/')}/chat/completions",
        json=body,
        headers=_headers(endpoint.api_key),
    )
    failed = _http_error(response)
    if failed:
        return failed
    try:
        message = response.json()["choices"][0]["message"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return f"answered 200, but not with a completion ({exc!r})"
    if not isinstance(message, dict):
        return "answered 200, but not with a completion (message is not an object)"
    return ""


_PROBES = {"embedding": _probe_embedding, "chat": _probe_chat}


async def _probe_one(
    client: httpx.AsyncClient, endpoint: "ModelEndpoint", timeout: float
) -> EndpointStatus:
    try:
        error = await _PROBES[endpoint.role](client, endpoint)
    except httpx.TimeoutException:
        error = f"no answer within {timeout:g}s"
    except Exception as exc:  # noqa: BLE001 — a health probe may never raise
        error = f"{type(exc).__name__}: {exc}"
    return EndpointStatus(
        role=endpoint.role,
        url=endpoint.api_base,
        model=endpoint.model,
        key_setting=endpoint.key_setting,
        error=error,
    )


async def probe(timeout: float = PROBE_TIMEOUT_S) -> ProbeResult:
    """One real call to each configured endpoint. Never raises, never gates.

    The import is deferred on purpose: a ``db`` deployment must not load the
    openviking module tree at all, and nothing here runs unless the caller has
    already checked which backend is configured.
    """
    from app.domain.memory.openviking_store import model_endpoints

    endpoints = model_endpoints()
    async with httpx.AsyncClient(timeout=timeout) as client:
        results = await asyncio.gather(
            *(_probe_one(client, endpoint, timeout) for endpoint in endpoints)
        )
    return ProbeResult(
        endpoints=tuple(results), checked_at=datetime.now(UTC).isoformat()
    )


class _Cache:
    """The last verdict, refreshed off the request path.

    A health endpoint that awaits a third party is a health endpoint that can
    be made to hang for as long as that third party likes. So readers always
    get the cached verdict: boot fills it in, and once it goes stale the
    refresh runs in the background while the stale answer is still served.
    """

    def __init__(self) -> None:
        self._result: ProbeResult | None = None
        self._stored_at = 0.0
        self._refresh: asyncio.Task[None] | None = None

    def store(self, result: ProbeResult) -> None:
        self._result = result
        self._stored_at = time.monotonic()

    async def current(self) -> ProbeResult:
        if self._result is None:
            result = await probe()
            self.store(result)
            return result
        if time.monotonic() - self._stored_at > PROBE_TTL_S:
            self._schedule_refresh()
        return self._result

    def _schedule_refresh(self) -> None:
        if self._refresh is not None and not self._refresh.done():
            return
        self._refresh = asyncio.create_task(self._refresh_now())

    async def _refresh_now(self) -> None:
        self.store(await probe())

    def reset(self) -> None:
        if self._refresh is not None and not self._refresh.done():
            self._refresh.cancel()
        self._result = None
        self._stored_at = 0.0
        self._refresh = None


_CACHE = _Cache()


def reset_cache() -> None:
    """Forget the cached verdict. For tests — a process only boots once."""
    _CACHE.reset()


async def memory_backend_health() -> dict[str, Any]:
    """The ``memory`` entry of ``/health/detailed``.

    On the db backend there is no model endpoint to check, so this reports
    ``skipped`` without touching the network or importing anything openviking.
    """
    if settings.memory_backend != "openviking":
        return {
            "status": "skipped",
            "backend": settings.memory_backend,
            "detail": "this backend calls no model endpoint",
        }
    return (await _CACHE.current()).as_dict()


async def check_on_startup() -> None:
    """Say it out loud, at boot, if the memory backend cannot reach its models.

    Never raises, and never delays boot past the probe timeout: the model
    vendor being down must not be able to stop this process from coming up and
    serving everything that has nothing to do with memory.
    """
    if settings.memory_backend != "openviking":
        return
    from app.domain.memory.openviking_store import FALLBACK_KEY_SETTING

    try:
        result = await probe()
    except Exception:  # noqa: BLE001 — boot continues regardless
        logger.exception("memory: could not probe the openviking model endpoints")
        return
    _CACHE.store(result)

    borrowed = [
        e.role for e in result.endpoints if e.key_setting == FALLBACK_KEY_SETTING
    ]
    if borrowed:
        logger.warning(
            "memory: the %s endpoint(s) fell back to the %s key. That token "
            "belongs to the agent gateway and speaks a different protocol than "
            "these endpoints do — set OPENVIKING_LLM_API_KEY and "
            "OPENVIKING_EMBEDDING_API_KEY explicitly.",
            "/".join(borrowed),
            FALLBACK_KEY_SETTING,
        )
    if result.ok:
        logger.info(
            "memory: openviking model endpoints answered — %s",
            ", ".join(f"{e.role} at {e.url}" for e in result.endpoints),
        )
        return
    logger.error(
        "memory: MEMORY_BACKEND=openviking but its model endpoints did not "
        "answer — %s. Nothing will be recorded and recall will stay empty, with "
        "no other symptom at all. Check OPENVIKING_LLM_API_KEY / "
        "OPENVIKING_EMBEDDING_API_KEY (and the matching _API_BASE / _MODEL "
        "settings), then redeploy — restarting the container does not re-read "
        "env_file. The current verdict is also served at /health/detailed "
        "under checks.memory.",
        result.summary(),
    )
