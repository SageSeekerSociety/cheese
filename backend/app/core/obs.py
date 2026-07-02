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

import structlog


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
