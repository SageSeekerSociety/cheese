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

from app.core import alerting

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


_ANSWERED_LIMIT = 200


def _what_the_server_answered(exc: BaseException | None) -> list[str]:
    """The status and body behind an HTTP error, which its own message omits.

    `httpx.HTTPStatusError` renders as `Client error '409 Conflict' for url
    '...'` — the status, and nothing the server actually said. The reason is in
    the body. On 2026-09-16 fifty of these were the device connection owner
    answering `device offline`; neither the alert nor the log line carried that
    word, so an error that named its own cause read as an unexplained one, and
    finding out took reading the owner's logs and asking it directly.

    Duck-typed on `.response` rather than importing httpx: this module sits
    below the HTTP client, and any client whose error carries a response gets
    the same treatment. The body is scrubbed — a 401 or a redirect can answer
    with the credential it rejected, and an alert reaches a chat group.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return []
    lines = []
    status = getattr(response, "status_code", None)
    if status is not None:
        lines.append(f"状态：{status}")
    try:
        body = response.text
    except Exception:  # noqa: BLE001 — a body that will not read is not the news
        return lines
    if not isinstance(body, str) or not body.strip():
        return lines
    # One line: an alert is a list of short lines, and a JSON body arrives with
    # newlines in it.
    body = " ".join(body.split())
    if len(body) > _ANSWERED_LIMIT:
        body = body[:_ANSWERED_LIMIT] + "…"
    lines.append(f"对方回答：{scrub_secrets(body)}")
    return lines


class AlertOnError(logging.Handler):
    """Put what the backend logs as an error where a person will actually see it.

    An error a browser shows is one way this platform fails. The other is
    background work failing where nobody is looking, and that is the one that
    went unnoticed: on 2026-09-16 the database refused 83 connections inside a
    single minute, and exactly ONE of them reached the request error handler.
    The rest were a harness poller, a hook-subscription recovery and two journal
    reads — each logged an exception and carried on, and the incident was found
    hours later by reading the logs. So this listens at the one place all of
    them already spoke: the root logger.

    `alerting.send` is fire-and-forget and carries its own budget (ten in five
    minutes, and it says so in the last one it sends), which is what keeps a
    storm of errors from becoming a storm of messages. With no webhook
    configured — every developer machine, every test — it is a no-op.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)

    @staticmethod
    def _summary(record: logging.LogRecord) -> tuple[str, list[str]]:
        # structlog hands the formatter a dict; uvicorn and friends hand it a
        # string. Both reach this handler, so both shapes are read here.
        event = record.msg
        context: dict = {}
        if isinstance(event, dict):
            context = event
            title = str(event.get("event", ""))
        else:
            try:
                title = record.getMessage()
            except Exception:  # noqa: BLE001 — a bad format string is not our bug
                title = str(event)
        lines = [f"来源：{record.name}"]
        for key in ("method", "path", "error", "topic", "device"):
            value = context.get(key)
            if value:
                lines.append(f"{key}：{value}")
        if record.exc_info and record.exc_info[0] is not None:
            lines.append(f"异常：{record.exc_info[0].__name__}")
            lines.extend(_what_the_server_answered(record.exc_info[1]))
        return title or record.name, lines

    def emit(self, record: logging.LogRecord) -> None:
        # The alerter logs its own failures; forwarding those would be a loop.
        if record.name.startswith("app.core.alerting"):
            return
        try:
            title, lines = self._summary(record)
            alerting.send(f"后端报错：{title}", lines)
        except Exception:  # noqa: BLE001 — logging must never raise into a caller
            pass


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
            # A plain traceback, chosen explicitly. ConsoleRenderer's default is
            # whatever it can find: with rich importable it renders through rich
            # with show_locals=True, and every frame's locals get pretty-printed
            # into the stream. A FastAPI handler's locals hold the resolved
            # Dependant tree, so ONE unhandled exception becomes tens of
            # thousands of lines, rendered and written synchronously on the event
            # loop. That is not slow logging, it is an outage: on 2026-08-20 the
            # dev box served nothing at all — /healthz included, so compose's
            # health gate could not even recreate the container — and each
            # timed-out request raised again and re-armed it.
            #
            # rich reaches the image transitively (openviking → typer → rich), so
            # this cannot be left to whether it happens to be installed.
            structlog.dev.ConsoleRenderer(
                colors=False, exception_formatter=structlog.dev.plain_traceback
            ),
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
    # Errors also go where a person is looking, not only into the stream.
    root.addHandler(AlertOnError())
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


class ResponseIntegrityAudit:
    """Did the body we promised actually leave this process?

    The `req` line logs status + duration the moment the handler returns — i.e.
    BEFORE a single byte of the body reaches the wire. So a response that the
    browser reports as `ERR_CONTENT_LENGTH_MISMATCH` (fewer bytes arrived than
    Content-Length declared) shows up here as a perfectly ordinary
    `req status=200 ms=60`, and the backend logs look innocent. That gap is
    exactly what makes an intermittent truncation impossible to place.

    This is pure ASGI (not BaseHTTPMiddleware) and mounted OUTERMOST, so it
    counts the bytes handed to the transport — after every other middleware has
    had its say. It only ever emits on an anomaly:

    - ``response truncated``: we sent fewer (or more) bytes than we declared.
      The cut is on OUR side of the proxy.
    - ``response aborted mid-body``: the send raised after headers went out —
      the connection died (peer reset, worker shutting down) while we were still
      writing. With ``proxy_buffering off`` on the /api/ location, nginx has
      already forwarded our Content-Length downstream, so the browser sees the
      mismatch rather than a clean 502.

    Silence here means the body left this process intact and the truncation
    happened further out (nginx ⇄ APISIX edge ⇄ browser).

    Responses with no Content-Length (streaming/SSE/WS) are not audited — there
    is nothing declared to compare against.
    """

    def __init__(self, app) -> None:  # noqa: ANN001 — ASGI app
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        log = get_logger("app.http")
        declared: int | None = None
        sent = 0
        status: int | None = None
        started = False

        async def _send(message) -> None:  # noqa: ANN001 — ASGI message
            nonlocal declared, sent, status, started
            if message["type"] == "http.response.start":
                started = True
                status = message.get("status")
                for key, value in message.get("headers") or ():
                    if key.lower() == b"content-length":
                        try:
                            declared = int(value)
                        except ValueError:
                            declared = None
                        break
            elif message["type"] == "http.response.body":
                sent += len(message.get("body") or b"")
            await send(message)

        path = scope.get("path", "")
        try:
            await self.app(scope, receive, _send)
        except Exception:
            if started:
                log.warning(
                    "response aborted mid-body",
                    path=path,
                    status=status,
                    declared=declared,
                    sent=sent,
                )
            raise

        if declared is not None and sent != declared:
            log.warning(
                "response truncated",
                path=path,
                status=status,
                declared=declared,
                sent=sent,
                missing=declared - sent,
            )
