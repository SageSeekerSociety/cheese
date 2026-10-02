"""RPC1 live reads. Content and mutations never pass through this client."""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.domain.agent.preview_hub import (
    PROBE_TIMEOUT_S,
    PreviewAdmissionError,
    PreviewHub,
)

AUDIENCE = "cheesex:preview-owner:inspect:v1"
INSPECT_PATH = "/_internal/preview/v1/inspect"


class InspectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    topic_id: uuid.UUID
    seat: str = Field(min_length=1, max_length=128)
    expected_instance: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    wait_ms: int = Field(default=0, ge=0, le=8000)
    probe: bool = False


class Inspection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Literal[1] = 1
    owner_incarnation: str
    transport_epoch: str | None = None
    state: Literal[
        "online",
        "transport_unavailable",
        "app_unavailable",
        "instance_gone",
        "instance_identity_unsupported",
    ]
    instance: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    capabilities: list[str] = Field(default_factory=list)

    @property
    def tunnel_up(self) -> bool:
        return self.state != "transport_unavailable"

    @property
    def alive(self) -> bool:
        return self.state in {"online", "instance_identity_unsupported"}


def service_token() -> str:
    now = int(time.time())
    return jwt.encode(
        {"aud": AUDIENCE, "sub": "preview-backend", "iat": now, "exp": now + 30},
        settings.preview_connection_auth_secret,
        algorithm="HS256",
    )


async def inspect_hub(
    hub: PreviewHub, incarnation: str, request: InspectRequest
) -> Inspection:
    """One captured transport and native instance, under one total budget."""
    unavailable = Inspection(
        owner_incarnation=incarnation, state="transport_unavailable"
    )
    if not hub.accepting:
        return unavailable
    try:
        async with asyncio.timeout(request.wait_ms / 1000 + PROBE_TIMEOUT_S):
            if request.wait_ms:
                await hub.wait_online(
                    request.topic_id, request.seat, request.wait_ms / 1000
                )
            machine = hub.machine(request.topic_id, request.seat)
            if machine is None or machine.stopped or not hub.accepting:
                return unavailable
            result = Inspection(
                owner_incarnation=incarnation,
                transport_epoch=machine.epoch,
                state="instance_identity_unsupported",
                capabilities=sorted(machine.capabilities),
            )

            def current() -> bool:
                return (
                    hub.accepting
                    and not machine.stopped
                    and hub.machine(request.topic_id, request.seat) is machine
                )

            if "instance-v1" in machine.capabilities:
                response = await hub.request_stream(
                    request.topic_id,
                    request.seat,
                    method="HEAD",
                    path="/",
                    headers=[],
                    inspect_instance=True,
                    machine=machine,
                    timeout=PROBE_TIMEOUT_S,
                )
                if response is None:
                    result.state = "app_unavailable"
                else:
                    try:
                        value = dict(response.headers).get("x-cheese-instance", "")
                        if len(value) == 64 and all(
                            c in "0123456789abcdef" for c in value
                        ):
                            result.instance = value
                            result.state = "online"
                        else:
                            result.state = "app_unavailable"
                    finally:
                        await response.aclose()
                if (
                    request.expected_instance
                    and result.instance != request.expected_instance
                ):
                    result.state = (
                        "instance_gone" if result.instance else "app_unavailable"
                    )
            if request.probe and result.alive:
                try:
                    response = await hub.request_stream(
                        request.topic_id,
                        request.seat,
                        method="GET",
                        path="/",
                        headers=[("host", "127.0.0.1")],
                        machine=machine,
                        instance=result.instance,
                        timeout=PROBE_TIMEOUT_S,
                        typed=True,
                    )
                except PreviewAdmissionError as exc:
                    result.state = exc.state
                    return result if current() else unavailable
                if response is None:
                    result.state = "app_unavailable"
                else:
                    try:
                        if response.status >= 500:
                            result.state = "app_unavailable"
                    finally:
                        await response.aclose()
                # A native listener can restart during a probe on the same helper.
                # The helper checks expected instance before opening its app socket.
            return result if current() else unavailable
    except TimeoutError:
        return unavailable


class PreviewOwnerClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        parsed = urlsplit(settings.preview_connection_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("owner mode requires a preview owner service origin")
        self.url = settings.preview_connection_url.rstrip("/") + INSPECT_PATH
        self.client = client

    async def inspect(
        self,
        topic_id: uuid.UUID,
        seat: str,
        *,
        expected_instance: str | None = None,
        wait_ms: int = 0,
        probe: bool = False,
    ) -> Inspection:
        request = InspectRequest(
            topic_id=topic_id,
            seat=seat,
            expected_instance=expected_instance,
            wait_ms=wait_ms,
            probe=probe,
        )
        unavailable = Inspection(
            owner_incarnation="unavailable", state="transport_unavailable"
        )
        try:
            async with asyncio.timeout(wait_ms / 1000 + PROBE_TIMEOUT_S + 1):
                async with self.client.stream(
                    "POST",
                    self.url,
                    json=request.model_dump(mode="json"),
                    headers={"Authorization": "Bearer " + service_token()},
                    timeout=wait_ms / 1000 + PROBE_TIMEOUT_S + 1,
                ) as response:
                    if response.status_code != 200:
                        return unavailable
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        body.extend(part)
                        if len(body) > 4096:
                            return unavailable
                    result = Inspection.model_validate_json(body)
                    if result.state == "online" and (
                        not result.instance
                        or not result.transport_epoch
                        or (expected_instance and result.instance != expected_instance)
                    ):
                        return unavailable
                    return result
        except (httpx.HTTPError, TimeoutError, ValueError):
            return unavailable


_client: PreviewOwnerClient | None = None


@asynccontextmanager
async def reuse_preview_owner_connections() -> AsyncIterator[None]:
    global _client
    if settings.preview_connection_mode == "legacy":
        yield
        return
    async with httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
    ) as client:
        _client = PreviewOwnerClient(client)
        try:
            yield
        finally:
            _client = None


async def inspect_owner(topic_id: uuid.UUID, seat: str, **kwargs) -> Inspection:
    # Fail closed even if backend startup hasn't initialized its owner client.
    if _client is None:
        return Inspection(
            owner_incarnation="unavailable", state="transport_unavailable"
        )
    return await _client.inspect(topic_id, seat, **kwargs)
