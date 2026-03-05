import time
import uuid

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import bind_context, clear_context, get_logger

logger = get_logger(__name__)

TRACE_ID_HEADER = "X-Trace-ID"
REQUEST_ID_HEADER = "X-Request-ID"


class TracingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        trace_id = request.headers.get(TRACE_ID_HEADER) or str(uuid.uuid4())
        request_id = str(uuid.uuid4())[:8]
        start_time = time.perf_counter()

        bind_context(
            trace_id=trace_id,
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        response_started = False
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message.get("status", 500)
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (TRACE_ID_HEADER.lower().encode(), trace_id.encode()),
                        (REQUEST_ID_HEADER.lower().encode(), request_id.encode()),
                    ]
                )
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            logger.info(
                "request_completed",
                status_code=status_code,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            logger.exception(
                "request_failed",
                error=str(exc),
                duration_ms=duration_ms,
            )
            raise
        finally:
            clear_context()
