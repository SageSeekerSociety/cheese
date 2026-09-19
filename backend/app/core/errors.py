import contextlib
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_412_PRECONDITION_FAILED,
    HTTP_422_UNPROCESSABLE_CONTENT,
    HTTP_429_TOO_MANY_REQUESTS,
    HTTP_500_INTERNAL_SERVER_ERROR,
    HTTP_502_BAD_GATEWAY,
    HTTP_503_SERVICE_UNAVAILABLE,
    HTTP_504_GATEWAY_TIMEOUT,
)


class BaseError(Exception):
    def __init__(self, status_code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.data = data

    @property
    def name(self) -> str:
        return self.__class__.__name__

    def to_response_body(self) -> dict:
        return {
            "code": self.status_code,
            "message": f"{self.name}: {self.args[0]}",
            "error": {
                "name": self.name,
                "message": self.args[0],
                "data": self.data,
            },
        }


class BadRequestError(BaseError):
    def __init__(self, message: str, data: Any | None = None) -> None:
        super().__init__(HTTP_400_BAD_REQUEST, message, data)


class NotFoundError(BaseError):
    def __init__(
        self, message: str = "Resource not found", data: Any | None = None
    ) -> None:
        super().__init__(HTTP_404_NOT_FOUND, message, data)

    @classmethod
    def for_resource(
        cls, resource_type: str, resource_id: int | str
    ) -> "NotFoundError":
        return cls(
            message=f"Resource {resource_type} not found",
            data={"type": resource_type, "id": resource_id},
        )


class ForbiddenError(BaseError):
    def __init__(self, message: str = "Access denied", data: Any | None = None) -> None:
        super().__init__(HTTP_403_FORBIDDEN, message, data)


class AuthenticationRequiredError(BaseError):
    def __init__(
        self, message: str = "Authentication required", data: Any | None = None
    ) -> None:
        super().__init__(HTTP_401_UNAUTHORIZED, message, data)


class ConflictError(BaseError):
    def __init__(self, message: str, data: Any | None = None) -> None:
        super().__init__(HTTP_409_CONFLICT, message, data)


class PreconditionFailedError(BaseError):
    def __init__(self, message: str, data: Any | None = None) -> None:
        super().__init__(HTTP_412_PRECONDITION_FAILED, message, data)


class UnprocessableEntityError(BaseError):
    def __init__(self, message: str, data: Any | None = None) -> None:
        super().__init__(HTTP_422_UNPROCESSABLE_CONTENT, message, data)


class InternalServerError(BaseError):
    def __init__(self, message: str = "Internal server error") -> None:
        super().__init__(HTTP_500_INTERNAL_SERVER_ERROR, message, None)


class AccessDeniedError(ForbiddenError):
    def __init__(
        self,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: int | str | None = None,
    ) -> None:
        data: dict[str, Any] = {}
        if action:
            data["action"] = action
        if resource_type:
            data["resourceType"] = resource_type
        if resource_id is not None:
            data["resourceId"] = resource_id
        super().__init__(message="Access denied", data=data or None)


class PermissionDeniedError(ForbiddenError):
    def __init__(
        self, message: str = "Permission denied", data: Any | None = None
    ) -> None:
        super().__init__(message, data)


class SudoRequiredError(ForbiddenError):
    """This operation needs a fresh re-authentication, not just a session.

    Distinct from a plain 403 because the client's answer is different: there
    is nothing wrong with who is asking, so the fix is to send them through
    the re-authentication screen and retry, not to tell them they lack
    permission. The class name travels in the response body, which is what
    the web client keys on.
    """


class TokenExpiredError(BaseError):
    def __init__(self, message: str = "Token has expired") -> None:
        super().__init__(HTTP_401_UNAUTHORIZED, message, None)


class InvalidTokenError(BaseError):
    def __init__(self, message: str = "Invalid token") -> None:
        super().__init__(HTTP_401_UNAUTHORIZED, message, None)


class NameAlreadyExistsError(ConflictError):
    def __init__(self, resource_type: str, name: str) -> None:
        super().__init__(
            message=f"{resource_type} with name {name} already exists",
            data={"type": resource_type, "name": name},
        )


class QuotaExceededError(BaseError):
    def __init__(self, message: str = "Quota exceeded") -> None:
        super().__init__(HTTP_429_TOO_MANY_REQUESTS, message, None)


class SystemBusyError(BaseError):
    def __init__(self, message: str = "System is busy, please try again later") -> None:
        super().__init__(HTTP_503_SERVICE_UNAVAILABLE, message, None)


class GatewayTimeoutError(BaseError):
    """Something we called did not answer in time. Not a fault of this server,
    and a 500 says it was — see the execution route and `device_connection_app`."""

    def __init__(self, message: str = "Upstream did not answer in time") -> None:
        super().__init__(HTTP_504_GATEWAY_TIMEOUT, message, None)


def format_error_response(status_code: int, message: str, name: str = "Error") -> dict:
    """The envelope every client of ours parses. ``name`` is what a caller
    switches on when the status alone does not say which condition it was."""
    return {
        "code": status_code,
        "message": f"{name}: {message}",
        "error": {
            "name": name,
            "message": message,
            "data": None,
        },
    }


async def base_error_handler(
    request: Request, exc: BaseError
) -> JSONResponse | PlainTextResponse:
    accept = request.headers.get("accept") or ""
    if "text/event-stream" in accept:
        body = f"event: error\ndata: {exc.args[0]}\n\n"
        return PlainTextResponse(
            content=body, status_code=exc.status_code, media_type="text/event-stream"
        )
    return JSONResponse(status_code=exc.status_code, content=exc.to_response_body())


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse | PlainTextResponse:
    accept = request.headers.get("accept") or ""
    detail = exc.detail if isinstance(exc.detail, str) else "HTTP error"
    # Whoever attached headers to the exception meant them to arrive. FastAPI's
    # own handler forwards `exc.headers`; this one replaces that handler, and
    # dropped them. The single place in this codebase that uses them is how the
    # connection owner says WHICH device went offline
    # (device_connection_app.py), so `DeviceOffline` never survived the trip:
    # the client read a 409 with no `X-Device-Id`, and an offline machine
    # arrived as a generic transport error instead. `pi/channel.py` treats those
    # two differently on purpose — 「not something waiting fixes」 — so a room
    # whose machine was simply off spent the full 120s startup wait retrying a
    # ping to a machine that was not there, and then failed under a name that
    # did not mention it.
    headers = getattr(exc, "headers", None)
    if "text/event-stream" in accept:
        body = f"event: error\ndata: {detail}\n\n"
        return PlainTextResponse(
            content=body,
            status_code=exc.status_code,
            media_type="text/event-stream",
            headers=headers,
        )
    return JSONResponse(
        status_code=exc.status_code,
        content=format_error_response(status_code=exc.status_code, message=detail),
        headers=headers,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse | PlainTextResponse:
    accept = request.headers.get("accept") or ""
    message = "Invalid request parameters"
    if "text/event-stream" in accept:
        body = f"event: error\ndata: {message}\n\n"
        return PlainTextResponse(
            content=body,
            status_code=HTTP_400_BAD_REQUEST,
            media_type="text/event-stream",
        )
    data = {"details": exc.errors()}
    body = BadRequestError(message, data=data).to_response_body()
    return JSONResponse(status_code=HTTP_400_BAD_REQUEST, content=body)


# ---------------------------------------------------------------------------
# cheesex 合并附加 (fusion §8.5 / I3-errors): the BaseError framework above is
# adopted as canonical (main's product raises it). These are the cheesex-only
# error names our agent/topic/chat code raises; they keep the {code,message,data}
# envelope via the AppError handler registered below. NotFoundError/ForbiddenError
# are NOT redefined here — cheesex call sites (raise NotFoundError("...")) are
# ctor-compatible with main's BaseError versions.
# ---------------------------------------------------------------------------
from fastapi import FastAPI  # noqa: E402

from app.core.obs import get_logger  # noqa: E402

_log = get_logger("app.errors")


class AppError(Exception):
    """Base for cheesex client-facing errors (kept for our agent/topic layer)."""

    code: int = 400
    message: str = "Bad request"

    def __init__(self, message: str | None = None) -> None:
        if message is not None:
            self.message = message
        super().__init__(self.message)


class ValidationError(AppError):
    code = 422
    message = "Validation failed"


class UnauthorizedError(AppError):
    code = 401
    message = "Unauthorized"


class GatewayUnavailableError(AppError):
    code = 503
    message = "AI gateway unavailable"


def closing_the_socket(handler):  # type: ignore[no-untyped-def]
    """The same handler, over a WebSocket connection: close it, answer nothing.

    A handler's answer is an HTTP response, and Starlette sends whatever a
    handler returns down the connection the exception came from — on a socket
    already accepted that is `websocket.http.response.start`, which uvicorn
    refuses with 「Expected ASGI message 'websocket.send' or 'websocket.close'」
    and logs as an application error. The connection owner hit it 1143 times
    on 2026-09-19 for one machine whose link died between `accept` and the
    welcome frame: `attach_device` raised DeviceOffline into the `/agent`
    route, the DeviceOffline handler answered 409, and the 409 had nowhere to
    go. Each one an alert, for a link that was simply already gone.

    Nothing about the condition changes: the socket is closed (a policy
    refusal before `accept` still arrives as the 403 it always was), the
    failure is logged once at WARNING with its name, and no response is sent.
    """

    async def handle(conn: Request, exc: Exception):  # type: ignore[no-untyped-def]
        if conn.scope["type"] != "websocket":
            return await handler(conn, exc)
        _log.warning(
            "websocket_closed_on_error",
            path=conn.url.path,
            error=type(exc).__name__,
            detail=str(exc)[:200],
        )
        with contextlib.suppress(Exception):  # the peer may be the one that left
            await conn.close(code=1011)  # type: ignore[attr-defined]
        return None

    return handle


def register_exception_handlers(app: FastAPI) -> None:
    """Register BOTH error frameworks (fusion merge): main's BaseError family +
    HTTP/validation handlers, and cheesex's AppError handler. Called from our
    main.py; main's product code raises BaseError, ours raises AppError."""
    # Imported here, not at module scope: `device_hub` sits above this module and
    # imports back through `app.core`, and nothing but this registration needs
    # the name.
    from app.domain.agent.device_hub import DeviceCallError, DeviceOffline

    async def _handle_device_offline(_: Request, exc: DeviceOffline) -> JSONResponse:
        """A machine that is off is an answer, not a fault of this server.

        It was reaching the catch-all below, so a laptop somebody closed came
        out as 500「服务器内部错误」 and, because an unhandled exception is
        logged on three separate ways out, as three alerts. Over the first 15
        hours of the alert channel that one condition was 162 of 600 messages —
        more than a quarter of everything the channel said, for a state with
        nothing to fix.

        409 with `X-Device-Id` is what the connection owner already answers on
        its own RPC path (`device_connection_app.call`), and what the clients
        already read to tell 「the machine is not there」 from 「the call went
        wrong」 — waiting fixes the second and never the first.
        """
        _log.warning("device_offline", device=exc.device_id)
        return JSONResponse(
            status_code=HTTP_409_CONFLICT,
            content=format_error_response(
                status_code=HTTP_409_CONFLICT,
                message=f"设备 {exc.device_id} 离线",
                name="DeviceOffline",
            ),
            headers={"X-Device-Id": exc.device_id},
        )

    async def _handle_device_call_error(
        request: Request, exc: DeviceCallError
    ) -> JSONResponse:
        """The machine answered, and its answer was a failure of its own.

        The words are the machine's — 「dial unix …sock: no such file」, 「lstat
        …/.cheese/executor: no such file」 — and they are what the person in
        the room can act on, so they travel: `name` says which condition this
        was and `message` carries them. 502 because the failure is on the far
        side of a gateway this process is; 500「服务器内部错误」 said the
        opposite, in both processes at once, and hid the words.

        The connection owner and the business backend share this registration
        (device_connection_app.py, main.py): the owner is what turns the hub's
        exception into the wire, and `DeviceHubRPC._request` turns the wire back
        into the same exception, so a caller reads one type whichever side of
        the owner it runs on.
        """
        _log.warning(
            "device_call_failed",
            path=request.url.path,
            method=request.method,
            error=str(exc),
        )
        return JSONResponse(
            status_code=HTTP_502_BAD_GATEWAY,
            content=format_error_response(
                status_code=HTTP_502_BAD_GATEWAY,
                message=str(exc),
                name="DeviceCallError",
            ),
        )

    async def _handle_client_disconnect(
        request: Request, _: ClientDisconnect
    ) -> JSONResponse:
        """The browser hung up while we were reading its request.

        Nothing failed here and nobody is left to answer: a tab closed
        mid-upload raises this, and answering 500 tells a client that is gone
        about a fault that did not happen — while the log line and its three
        alerts describe our own server to whoever is on call. Registered rather
        than caught in the handler below because a handler registered for a
        specific type runs INSIDE the request middleware, so the request log
        never sees a failure either.
        """
        _log.info("client_disconnected", path=request.url.path, method=request.method)
        return JSONResponse(
            status_code=499,
            content=format_error_response(
                status_code=499, message="客户端已断开", name="ClientDisconnect"
            ),
        )

    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.code,
            content={"code": exc.code, "message": exc.message, "data": None},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Anything no handler above claimed.

        Without this, Starlette answers with a 21-byte ``Internal Server Error``
        in plain text — a shape no client of ours can read, from a failure
        nobody logged. The two facts that make such a 500 diagnosable are the
        traceback in the backend's log and the same ``{code, message, data}``
        envelope every other error uses, so the frontend reports 「出错了」
        rather than parsing a JSON that isn't there.

        The message is deliberately generic: what actually went wrong belongs in
        the log, not in a response to whoever asked."""
        _log.exception(
            "unhandled_error",
            path=request.url.path,
            method=request.method,
            error=type(exc).__name__,
        )
        return JSONResponse(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "code": HTTP_500_INTERNAL_SERVER_ERROR,
                "message": "服务器内部错误",
                "data": None,
            },
        )

    for exc_type, handler in (
        (DeviceOffline, _handle_device_offline),
        (DeviceCallError, _handle_device_call_error),
        (ClientDisconnect, _handle_client_disconnect),
        (AppError, _handle_app_error),
        (BaseError, base_error_handler),
        (StarletteHTTPException, http_exception_handler),
        (RequestValidationError, validation_exception_handler),
    ):
        app.add_exception_handler(exc_type, closing_the_socket(handler))  # type: ignore[arg-type]
