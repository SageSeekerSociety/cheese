"""Check upstream boundary logs and stream behavior without a provider call."""

import asyncio
import io
import json
import logging
import socket
import ssl
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
    # Errors retain their original exception and log only a safe classification.
    for cause, expected in (
        (socket.gaierror(-2, "secret DNS address"), "dns"),
        (ssl.SSLError("secret certificate"), "tls"),
        (httpx.ConnectTimeout("secret timeout"), "timeout"),
    ):
        records = []
        error = httpx.ConnectError("secret transport")
        error.__cause__ = cause

        async def fail(*args, **kwargs):
            raise error

        with patch.object(
            timing.logger, "info", lambda _, value: records.append(json.loads(value))
        ):
            try:
                await timing.trace_post(fail)(
                    None, logging_obj=SimpleNamespace(litellm_call_id="failure")
                )
            except httpx.ConnectError as caught:
                assert caught is error
            else:
                raise AssertionError("Original error swallowed")
        assert records[-1]["category"] == expected
        assert "secret" not in json.dumps(records)
    for status, code, message, expected in (
        (429, "1113", "[1113][余额不足或无可用资源包,请充值。] secret body", "provider_quota"),
        (400, "invalid_request_error", "Insufficient Balance secret body", "provider_quota"),
        (429, None, "secret body", "rate_limit"),
        (401, None, "secret body", "http_auth"),
        (500, None, "secret body", "http_error"),
    ):
        response = httpx.Response(
            status,
            json={"error": {"code": code, "message": message}},
            request=httpx.Request("POST", "https://secret.invalid"),
        )
        error = httpx.HTTPStatusError(
            "secret HTTP error", request=response.request, response=response
        )
        records = []

        async def fail_http(*args, **kwargs):
            raise error

        with patch.object(
            timing.logger, "info", lambda _, value: records.append(json.loads(value))
        ):
            try:
                await timing.trace_post(fail_http)(
                    None, logging_obj=SimpleNamespace(litellm_call_id="http-failure")
                )
            except httpx.HTTPStatusError as caught:
                assert caught is error
        assert records[-1]["category"] == expected
        assert "secret" not in json.dumps(records)
    # A refusal names the limit behind it, never the message, which names the key.
    for message, expected in (
        (
            "Rate limit exceeded for api_key: secret-key-hash. Limit type: "
            "max_parallel_requests. Current limit: 3, Remaining: 0. Limit resets at: "
            "secret time",
            {"limit_type": "max_parallel_requests", "limit": 3, "remaining": 0},
        ),
        (
            "No deployments available for selected model, secret detail",
            {"limit_type": "upstream_no_deployment"},
        ),
        ("secret unrecognised refusal", {}),
    ):
        response = httpx.Response(
            429,
            json={"error": {"type": "rate_limit_error", "message": message}},
            request=httpx.Request("POST", "https://secret.invalid"),
        )
        error = httpx.HTTPStatusError(
            "secret HTTP error", request=response.request, response=response
        )
        records = []

        async def refuse(*args, **kwargs):
            raise error

        with patch.object(
            timing.logger, "info", lambda _, value: records.append(json.loads(value))
        ):
            try:
                await timing.trace_post(refuse)(
                    None, logging_obj=SimpleNamespace(litellm_call_id="refused")
                )
            except httpx.HTTPStatusError:
                pass
        end = records[-1]
        assert end["category"] == "rate_limit", end
        assert {k: end[k] for k in expected} == expected, end
        assert not {"limit_type", "limit", "remaining"} - set(expected) & set(end), end
        assert "secret" not in json.dumps(records)
    # Both records of a call say which deployment, group, conversation and attempt.
    for session, logged in (
        ("0f1e2d3c-1111-2222-3333-444455556666", "0f1e2d3c-1111-2222-3333-444455556666"),
        ("secret value\r\nx: y", None),
    ):
        records = []
        attempt = SimpleNamespace(
            litellm_call_id="attributed",
            litellm_params={
                "model_info": {"id": "deployment-9"},
                "metadata": {
                    "model_group": "deepseek-flash",
                    "session_id": session,
                    "attempted_retries": 1,
                    "attempted_fallbacks": 0,
                },
            },
        )

        async def served(*args, **kwargs):
            return httpx.Response(200, json={}, request=httpx.Request("POST", "https://secret.invalid"))

        with patch.object(
            timing.logger, "info", lambda _, value: records.append(json.loads(value))
        ):
            await timing.trace_post(served)(None, logging_obj=attempt)
        for record in (records[0], records[-1]):
            assert record["deployment"] == "deployment-9", record
            assert record["model_group"] == "deepseek-flash", record
            assert record["attempted_retries"] == 1, record
            assert record["attempted_fallbacks"] == 0, record
            assert record.get("session") == logged, record
        assert [r["phase"] for r in records][0] == "request_start"
        assert records[-1]["phase"] == "request_end"
        assert "secret" not in json.dumps(records)
    print("Refusals name their limit; records name deployment, group, session, attempt")
    unread = httpx.Response(429, stream=Body("complete"))
    assert timing.failure_fields(response=unread)["category"] == "rate_limit"
    assert not unread.is_stream_consumed
    await unread.aclose()
    records = []
    unread = httpx.Response(429, stream=Body("complete"))

    async def rejected(*args, **kwargs):
        return unread

    with patch.object(
        timing.logger, "info", lambda _, value: records.append(json.loads(value))
    ):
        response = await timing.trace_post(rejected)(
            None, logging_obj=SimpleNamespace(litellm_call_id="unread-429")
        )
        assert not response.is_stream_consumed
        assert (
            b"".join([chunk async for chunk in response.aiter_bytes()]) == b"firstlast"
        )
        await response.aclose()
    assert records[-1]["outcome"] == "http_error"
    assert records[-1]["category"] == "rate_limit"
    assert records[-1]["status"] == 429
    print("Failure classification preserves exceptions and unread bodies")
    # Distinguish awaiting upstream bytes from time held by the stream consumer.
    for close_early in (False, True):
        clock = [0.0]
        records = []

        class ControlledBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                clock[0] += 4
                yield b"first"
                clock[0] += 6
                yield b"last"

            async def aclose(self):
                pass

        with (
            patch.object(
                timing,
                "time",
                SimpleNamespace(monotonic=lambda: clock[0], time=lambda: clock[0]),
            ),
            patch.object(
                timing.logger,
                "info",
                lambda _, value: records.append(json.loads(value)),
            ),
        ):
            measured = timing.RequestTiming("consumer-boundary")
            stream = timing.TimedStream(ControlledBody(), measured)
            iterator = stream.__aiter__()
            assert await anext(iterator) == b"first"
            clock[0] += 3
            if close_early:
                await stream.aclose()
                await iterator.aclose()
            else:
                assert await anext(iterator) == b"last"
                clock[0] += 7
                try:
                    await anext(iterator)
                except StopAsyncIteration:
                    pass
                else:
                    raise AssertionError("Stream did not finish")
                await stream.aclose()
        ends = [record for record in records if record["phase"] == "request_end"]
        assert len(ends) == 1
        assert ends[0]["consumer_hold_ms"] == (3000 if close_early else 10000)
        assert ends[0]["elapsed_ms"] == (7000 if close_early else 20000)
        assert ends[0]["outcome"] == (
            "closed_before_eof" if close_early else "complete"
        )
        # The first upstream byte is marked exactly once, before the end, so the
        # gap a person waits through can be split from the generation after it.
        firsts = [record for record in records if record["phase"] == "first_body"]
        assert len(firsts) == 1, records
        assert firsts[0]["elapsed_ms"] == 4000, firsts[0]
        assert [r["phase"] for r in records].index("first_body") < [
            r["phase"] for r in records
        ].index("request_end")
        print(f"Consumer hold timing passed: close_early={close_early}")
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
