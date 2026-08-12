"""Observability (可观测性军规): 事事有日志、可查询、可关联.

- structlog renders every line as timestamped key=value (grep-able now,
  JSON-flippable later for aggregation).
- contextvars carry correlation ids: an HTTP request binds request_id, an agent
  turn binds turn/topic/project — every log line inside that async context
  carries them automatically (async-safe; no id threading through call args).
- stdlib logging (uvicorn, libraries) passes through the same formatter, so one
  stream holds everything, uniformly timestamped.
"""

import logging
import re
from typing import Any

import structlog

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


def scrub_secrets(value: Any) -> Any:
    """Public because the log stream is no longer the only place credentials can
    surface: `domain.backend_log` pushes tracebacks into a room, where everyone
    can read them. Same filter, so a shape only has to be recognized once."""
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
        record.msg = scrub_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: scrub_secrets(v) for k, v in record.args.items()}
            else:
                record.args = tuple(scrub_secrets(a) for a in record.args)
        return True


def configure_logging() -> None:
    """Idempotent process-wide logging setup. Call before the app starts."""
    timestamper = structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False)
    pre_chain = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]

    structlog.configure(
        processors=[
            *pre_chain,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # stdlib records (uvicorn etc.) get the same rendering + timestamps.
        foreign_pre_chain=pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    # On the handler, not on our own call sites: the token leak came from
    # uvicorn's access logger — code we never call — and everything reaches a
    # handler.
    handler.addFilter(RedactSecrets())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # uvicorn installs its own handlers; route them through ours instead so the
    # whole stream is uniform (and timestamped).
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True


def get_logger(name: str):  # noqa: ANN201 — structlog's own typing
    return structlog.get_logger(name)


def bind_context(**kwargs) -> None:
    """Attach ids to the CURRENT async context (request / turn). Every log line
    emitted within it — any module, any depth — carries them."""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context(*keys: str) -> None:
    structlog.contextvars.unbind_contextvars(*keys)
