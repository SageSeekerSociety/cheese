"""Measure upstream HTTP boundaries without logging request contents."""

import functools
import json
import logging
import time

import httpx

logger = logging.getLogger("cheese.provider_http_timing")
# The proxy may suppress INFO globally. Keep these metadata-only records visible
# without enabling provider debug logging, which can include request contents.
logger.setLevel(logging.INFO)
logger.propagate = False
logger.addHandler(logging.StreamHandler())


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

    def finish(self, outcome):
        if not self.finished:
            self.finished = True
            held = self.consumer_hold
            if self.consumer_since is not None:
                held += time.monotonic() - self.consumer_since
            self.emit("request_end", outcome=outcome, consumer_hold_ms=held * 1000)


class TimedStream(httpx.AsyncByteStream):
    def __init__(self, stream, timing):
        self.stream = stream
        self.timing = timing

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
        except BaseException:
            self.timing.finish("interrupted")
            raise
        else:
            self.timing.finish("complete")

    async def aclose(self):
        try:
            await self.stream.aclose()
        finally:
            self.timing.finish("closed_before_eof")


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
        except BaseException:
            timing.finish("request_failed")
            raise
        timing.emit(
            "response_buffered" if response.is_stream_consumed else "response_headers",
            status=response.status_code,
        )
        if response.is_stream_consumed:
            timing.finish("complete")
        else:
            response.stream = TimedStream(response.stream, timing)
        return response

    return traced
