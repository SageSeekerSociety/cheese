from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_412_PRECONDITION_FAILED,
    HTTP_422_UNPROCESSABLE_ENTITY,
    HTTP_429_TOO_MANY_REQUESTS,
    HTTP_500_INTERNAL_SERVER_ERROR,
    HTTP_503_SERVICE_UNAVAILABLE,
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
    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(HTTP_401_UNAUTHORIZED, message, None)


class ConflictError(BaseError):
    def __init__(self, message: str, data: Any | None = None) -> None:
        super().__init__(HTTP_409_CONFLICT, message, data)


class PreconditionFailedError(BaseError):
    def __init__(self, message: str, data: Any | None = None) -> None:
        super().__init__(HTTP_412_PRECONDITION_FAILED, message, data)


class UnprocessableEntityError(BaseError):
    def __init__(self, message: str, data: Any | None = None) -> None:
        super().__init__(HTTP_422_UNPROCESSABLE_ENTITY, message, data)


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


def format_error_response(status_code: int, message: str) -> dict:
    name = "Error"
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
    if "text/event-stream" in accept:
        body = f"event: error\ndata: {detail}\n\n"
        return PlainTextResponse(
            content=body, status_code=exc.status_code, media_type="text/event-stream"
        )
    return JSONResponse(
        status_code=exc.status_code,
        content=format_error_response(status_code=exc.status_code, message=detail),
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


def register_exception_handlers(app: FastAPI) -> None:
    """Register BOTH error frameworks (fusion merge): main's BaseError family +
    HTTP/validation handlers, and cheesex's AppError handler. Called from our
    main.py; main's product code raises BaseError, ours raises AppError."""

    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.code,
            content={"code": exc.code, "message": exc.message, "data": None},
        )

    app.add_exception_handler(BaseError, base_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
