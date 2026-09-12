"""Check upstream boundary logs and stream behavior without a provider call."""

import asyncio
import io
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from litellm.llms.custom_httpx import provider_http_timing as timing
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler


class Body(httpx.AsyncByteStream):
    def __init__(self, mode):
        self.mode = mode
        self.closed = False

    async def __aiter__(self):
        yield b"first"
        await asyncio.sleep(0.02)
        if self.mode == "broken":
            raise httpx.ReadError("test stream interrupted")
        if self.mode == "cancelled":
            raise asyncio.CancelledError()
        yield b"last"

    async def aclose(self):
        self.closed = True


async def main():
    capture = io.StringIO()
    proxy_logger = logging.getLogger("LiteLLM Proxy")
    with (
        patch.object(proxy_logger, "level", logging.ERROR),
        patch.object(timing.logger.handlers[0], "stream", capture),
    ):
        timing.RequestTiming("visibility-check").finish("complete")
    visible = capture.getvalue()
    assert '"call_id": "visibility-check"' in visible
    assert '"phase": "request_end"' in visible
    handler = AsyncHTTPHandler()
    try:
        for mode in ("complete", "close", "broken", "cancelled", "failed"):
            body = Body(mode)
            records = []

            async def send(request, **kwargs):
                if mode == "failed":
                    raise ValueError("test request failure")
                return httpx.Response(200, stream=body, request=request)

            def log(_, value):
                records.append(json.loads(value))

            response = None
            received = b""
            caught = None
            with (
                patch.object(handler.client, "send", send),
                patch.object(timing.logger, "info", log),
            ):
                try:
                    response = await handler.post(
                        "https://example.invalid/test",
                        data="secret-body",
                        headers={"Authorization": "secret-key"},
                        stream=True,
                        logging_obj=SimpleNamespace(litellm_call_id=mode),
                    )
                    if mode != "close":
                        async for chunk in response.aiter_bytes():
                            received += chunk
                except (httpx.ReadError, asyncio.CancelledError, ValueError) as exc:
                    caught = type(exc)
                finally:
                    if response is not None:
                        await response.aclose()
            ends = [r for r in records if r["phase"] == "request_end"]
            assert len(ends) == 1, records
            expected = {
                "complete": "complete",
                "close": "closed_before_eof",
                "broken": "interrupted",
                "cancelled": "interrupted",
                "failed": "request_failed",
            }[mode]
            assert ends[0]["outcome"] == expected
            assert all(r["call_id"] == mode for r in records)
            assert "secret" not in json.dumps(records)
            if mode == "complete":
                assert received == b"firstlast"
                assert ends[0]["elapsed_ms"] >= 20
            if mode == "broken":
                assert caught is httpx.ReadError
            if mode == "cancelled":
                assert caught is asyncio.CancelledError
            if mode == "failed":
                assert caught is ValueError
            else:
                assert body.closed
            print(f"Upstream HTTP timing passed: {mode}")
    finally:
        await handler.client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
