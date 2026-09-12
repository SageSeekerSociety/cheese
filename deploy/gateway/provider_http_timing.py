"""Measure upstream HTTP boundaries without logging request contents."""

import functools
import json
import logging
import time

import httpx

logger = logging.getLogger("LiteLLM Proxy")


class RequestTiming:
    def __init__(self, call_id):
        self.call_id = call_id
        self.started = time.monotonic()
        self.finished = False
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
            self.emit("request_end", outcome=outcome)


class TimedStream(httpx.AsyncByteStream):
    def __init__(self, stream, timing):
        self.stream = stream
        self.timing = timing

    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                yield chunk
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
