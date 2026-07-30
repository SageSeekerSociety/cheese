import logging
import re
import sys
from typing import Any

import structlog
from structlog.types import Processor

from app.core.config import settings

# Query-string parameters whose value is a credential. `token` is the one that
# actually leaked: browsers cannot set an Authorization header on a WebSocket, so
# every WS carries `?token=<jwt>` — and uvicorn's access log prints the full URL,
# which put live session tokens in plaintext in `docker logs`. Anyone who could
# read the logs could impersonate the user.
_SECRET_PARAMS = (
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "code",
)
_SECRET_RE = re.compile(
    r"\b(" + "|".join(_SECRET_PARAMS) + r")=([^&\s\"']+)", re.IGNORECASE
)


def _scrub(value: Any) -> Any:
    if isinstance(value, str) and "=" in value:
        return _SECRET_RE.sub(r"\1=***", value)
    return value


class RedactSecrets(logging.Filter):
    """Strip credentials out of every log record, whoever emitted it.

    Applied to the root handler rather than to our own call sites: the leak came
    from uvicorn's access logger, i.e. code we do not call. Anything that reaches
    a handler goes through here.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _scrub(v) for k, v in record.args.items()}
            else:
                record.args = tuple(_scrub(a) for a in record.args)
        return True


def setup_logging() -> None:
    """Configure structured logging with structlog."""
    is_prod = settings.environment == "production"

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if is_prod:
        shared_processors.append(structlog.processors.format_exc_info)
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(RedactSecrets())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG if not is_prod else logging.INFO)

    for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error", "sqlalchemy"]:
        logging.getLogger(logger_name).handlers.clear()
        logging.getLogger(logger_name).propagate = True

    if is_prod:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger."""
    return structlog.get_logger(name)


def bind_context(**kwargs: Any) -> None:
    """Bind context variables for the current async context."""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """Clear all context variables."""
    structlog.contextvars.clear_contextvars()
