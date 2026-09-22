"""Measure upstream HTTP boundaries without logging request contents."""

import functools
import asyncio
import json
import logging
import time
import socket
import ssl

import httpx

logger = logging.getLogger("cheese.provider_http_timing")
# The proxy may suppress INFO globally. Keep these metadata-only records visible
# without enabling provider debug logging, which can include request contents.
logger.setLevel(logging.INFO)
logger.propagate = False
logger.addHandler(logging.StreamHandler())


def failure_fields(error=None, response=None):
    """Classify evidence already available without reading ahead or logging bodies."""
    chain, seen = [], set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    for kind, category in (
        (asyncio.CancelledError, "cancelled"),
        (socket.gaierror, "dns"),
        (ssl.SSLError, "tls"),
        ((TimeoutError, httpx.TimeoutException), "timeout"),
    ):
        if any(isinstance(item, kind) for item in chain):
            return {"category": category}
    if response is None:
        response = getattr(error, "response", None)
    if response is not None and response.status_code >= 400:
        code = None
        if response.is_stream_consumed:
            try:
                if len(response.content) <= 65536:
                    body = response.json()
                    detail = body.get("error", {}) if isinstance(body, dict) else {}
                    candidate = detail.get("code") if isinstance(detail, dict) else None
                    if str(candidate) in {"1113", "insufficient_quota"}:
                        code = str(candidate)
            except (ValueError, httpx.ResponseNotRead):
                pass
        status = response.status_code
        category = (
            "provider_quota"
            if code
            else "http_auth"
            if status in (401, 403)
            else "rate_limit"
            if status == 429
            else "http_error"
        )
        return {"category": category, "status": status, "provider_code": code}
    for kind, category in (
        (httpx.ConnectError, "connection"),
        ((httpx.ReadError, httpx.RemoteProtocolError), "stream_transport"),
        (ConnectionError, "connection"),
    ):
        if any(isinstance(item, kind) for item in chain):
            return {"category": category}
    return {"category": "unknown_error"}


class RequestTiming:
    def __init__(self, call_id):
        self.call_id = call_id
        self.started = time.monotonic()
        self.finished = False
        self.consumer_hold = 0.0
        self.consumer_since = None
        self.emit("request_start")

    def emit(self, phase, **fields):
        logger.info(
            "provider_http_timing %s",
            json.dumps(
                {
                    "call_id": self.call_id,
                    "phase": phase,
                    "unix_ms": time.time() * 1000,
                    "elapsed_ms": (time.monotonic() - self.started) * 1000,
                    **fields,
                }
            ),
        )

    def finish(self, outcome, **fields):
        if not self.finished:
            self.finished = True
            held = self.consumer_hold
            if self.consumer_since is not None:
                held += time.monotonic() - self.consumer_since
            self.emit(
                "request_end", outcome=outcome, consumer_hold_ms=held * 1000, **fields
            )


class TimedStream(httpx.AsyncByteStream):
    def __init__(self, stream, timing, failure=None):
        self.stream = stream
        self.timing = timing
        self.failure = failure or {}

    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                # Downstream holds may overlap upstream work; they are not CPU time.
                started = time.monotonic()
                self.timing.consumer_since = started
                try:
                    yield chunk
                finally:
                    self.timing.consumer_hold += time.monotonic() - started
                    self.timing.consumer_since = None
        except BaseException as error:
            self.timing.finish(
                "interrupted", **{**self.failure, **failure_fields(error)}
            )
            raise
        else:
            self.timing.finish(
                "http_error" if self.failure else "complete", **self.failure
            )

    async def aclose(self):
        try:
            await self.stream.aclose()
        finally:
            self.timing.finish("closed_before_eof", **self.failure)


def trace_post(post):
    @functools.wraps(post)
    async def traced(self, *args, **kwargs):
        # LiteLLM's model HTTP handler supplies this keyword; unrelated HTTP
        # calls without a model logging object retain their original path.
        logging_obj = kwargs.get("logging_obj")
        if logging_obj is None:
            return await post(self, *args, **kwargs)
        timing = RequestTiming(logging_obj.litellm_call_id)
        try:
            response = await post(self, *args, **kwargs)
        except BaseException as error:
            timing.finish("request_failed", **failure_fields(error))
            raise
        timing.emit(
            "response_buffered" if response.is_stream_consumed else "response_headers",
            status=response.status_code,
        )
        if response.is_stream_consumed:
            if response.status_code >= 400:
                timing.finish("http_error", **failure_fields(response=response))
            else:
                timing.finish("complete")
        else:
            response.stream = TimedStream(
                response.stream,
                timing,
                failure_fields(response=response)
                if response.status_code >= 400
                else None,
            )
        return response

    return traced
