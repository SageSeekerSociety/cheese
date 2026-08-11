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

# Credential-bearing key names. `token` is the one that actually leaked: browsers
# cannot set an Authorization header on a WebSocket, so every WS carries
# `?token=<jwt>` — and uvicorn's access log prints the full URL, which put live
# session tokens in plaintext in `docker logs`. Anyone who could read the logs
# could impersonate the user.
#
# These match as the TAIL of an identifier (`anthropic_auth_token`, `db_password`)
# because that is how they appear in a repr. `code` is the exception: it is here
# only for the OAuth device flow's `?code=`, and matching it as a tail would
# scrub `status_code=500` out of every traceback that has one.
_SECRET_KEY_TAILS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "credential",
)
_SECRET_KEY_EXACT = ("code",)
_SECRET_KEY = (
    r"(?:[A-Za-z0-9]+_)*(?:"
    + "|".join(_SECRET_KEY_TAILS)
    + r")|"
    + "|".join(_SECRET_KEY_EXACT)
)
# `key=value`, `key: value`, and the QUOTED forms a repr produces: `token='x'`,
# `{"token": "x"}`. The original pattern excluded quotes from the value, which
# meant it matched the query-string form and nothing else — a repr put the quote
# right where the value should start, so it matched zero characters and gave up.
_SECRET_KV_RE = re.compile(
    # The trailing `\3?` eats the value's own closing quote so the replacement
    # can put one back — otherwise a repr comes out as `token='***''`.
    r"\b(" + _SECRET_KEY + r")([\"']?\s*[=:]\s*)([\"']?)[^\"'\s,;&}\])>]+\3?",
    re.IGNORECASE,
)
# `postgresql://user:pw@host` — the shape a DSN takes in the traceback of a
# database that would not connect, which is exactly when you read one.
_URL_CRED_RE = re.compile(r"(://[^\s:/@]+:)[^\s@/]+(@)")
# `Bearer <token>` carries no key name at all, so no key list can reach it.
_AUTH_SCHEME_RE = re.compile(
    r"\b(bearer|basic)(\s+)[A-Za-z0-9._~+/=-]{4,}", re.IGNORECASE
)
# Last line: match the VALUE's shape, no key name involved. This is the only rule
# that survives a field name nobody predicted — and in a traceback the field
# names are unpredictable by construction (locals, reprs, third-party kwargs).
_SECRET_VALUE_RE = re.compile(
    r"\b(?:"
    r"sk-[A-Za-z0-9_-]{8,}"  # Anthropic / OpenAI style
    r"|gh[pousr]_[A-Za-z0-9]{8,}"  # GitHub PAT / OAuth / server / refresh
    r"|github_pat_[A-Za-z0-9_]{8,}"
    r"|xox[abprs]-[A-Za-z0-9-]{8,}"  # Slack
    r"|AKIA[0-9A-Z]{12,}"  # AWS access key id
    r"|eyJ[A-Za-z0-9_-]{6,}(?:\.[A-Za-z0-9_-]+){2}"  # JWT
    r")"
)


def scrub_secrets(value: Any) -> Any:
    """Strip credentials out of a string, whatever shape they arrive in.

    Public because the log stream is no longer the only place credentials can
    surface: `domain.backend_log` pushes tracebacks into a room, where everyone
    can read them — and unlike `docker logs`, that audience does NOT already
    hold the keys. Same filter, so a shape only has to be recognized once.

    Four passes, because one pattern cannot see all four shapes. `Bearer` runs
    before the key/value pass so the latter cannot eat the scheme word and leave
    the credential behind it exposed.
    """
    if not isinstance(value, str):
        return value
    out = _AUTH_SCHEME_RE.sub(r"\1\2***", value)
    out = _SECRET_KV_RE.sub(r"\1\2\3***\3", out)
    out = _URL_CRED_RE.sub(r"\1***\2", out)
    return _SECRET_VALUE_RE.sub("***", out)


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
