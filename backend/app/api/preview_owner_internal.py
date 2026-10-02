"""Private RPC1 read admission; deliberately outside router auto-discovery."""

import asyncio

import anyio
import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from app.core.config import settings
from app.domain.agent.preview_hub import PreviewHub
from app.domain.agent.preview_owner import (
    AUDIENCE,
    INSPECT_PATH,
    InspectRequest,
    inspect_hub,
)


def inspector_router(hub: PreviewHub, incarnation: str) -> APIRouter:
    router = APIRouter()
    active: set[asyncio.Task] = set()

    @router.post(INSPECT_PATH)
    async def inspect(request: Request) -> dict:
        authorization = request.headers.get("authorization", "")
        try:
            if not authorization.startswith("Bearer "):
                raise ValueError("missing service authentication")
            claims = jwt.decode(
                authorization[7:],
                settings.preview_connection_auth_secret,
                algorithms=["HS256"],
                audience=AUDIENCE,
                options={"require": ["exp", "iat", "sub", "aud"], "strict_aud": True},
            )
            if claims["sub"] != "preview-backend" or claims["exp"] - claims["iat"] > 30:
                raise ValueError("invalid service authentication")
        except (jwt.PyJWTError, ValueError, TypeError):
            raise HTTPException(403, "invalid preview service authentication") from None
        if not hub.accepting or len(active) >= 64:
            raise HTTPException(
                503, "preview owner unavailable", headers={"Retry-After": "1"}
            )
        task = asyncio.current_task()
        assert task is not None
        active.add(task)
        try:
            body = bytearray()
            async with asyncio.timeout(1):
                async for part in request.stream():
                    body.extend(part)
                    if len(body) > 1024:
                        raise HTTPException(413, "preview inspection too large")
            try:
                query = InspectRequest.model_validate_json(body)
            except ValidationError:
                raise HTTPException(422, "invalid preview inspection") from None
            work = asyncio.create_task(inspect_hub(hub, incarnation, query))

            async def disconnected() -> None:
                while (await request.receive())["type"] != "http.disconnect":
                    pass

            gone = asyncio.create_task(disconnected())
            try:
                done, _ = await asyncio.wait(
                    {work, gone}, return_when=asyncio.FIRST_COMPLETED
                )
                if gone in done:
                    raise HTTPException(503, "preview inspection disconnected")
                return (await work).model_dump()
            finally:
                work.cancel()
                gone.cancel()
                with anyio.CancelScope(shield=True):
                    await asyncio.gather(work, gone, return_exceptions=True)
        except TimeoutError:
            raise HTTPException(408, "preview inspection timed out") from None
        finally:
            active.discard(task)

    return router
