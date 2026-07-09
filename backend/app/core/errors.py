"""Domain error classes and FastAPI exception handlers.

Responses follow the platform envelope: {"code", "message", "data"}.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base class for expected, client-facing errors."""

    code: int = 400
    message: str = "Bad request"

    def __init__(self, message: str | None = None):
        if message is not None:
            self.message = message
        super().__init__(self.message)


class NotFoundError(AppError):
    code = 404
    message = "Resource not found"


class ValidationError(AppError):
    code = 422
    message = "Validation failed"


class ForbiddenError(AppError):
    code = 403
    message = "Forbidden"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.code,
            content={"code": exc.code, "message": exc.message, "data": None},
        )
