"""Exercise connection retries against a local streaming HTTP server."""

import asyncio
from unittest.mock import patch

import httpx

from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler


async def check(mode, initial_error=httpx.ConnectError):
    release = asyncio.Event()
    finished = asyncio.Event()

    async def serve(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            status = b"503 Unavailable" if mode == "http_error" else b"200 OK"
            writer.write(b"HTTP/1.1 " + status + b"\r\nContent-Length: 10\r\n\r\nfirst")
            await writer.drain()
            await release.wait()
            if mode != "broken":
                writer.write(b"last!")
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    handler = AsyncHTTPHandler(timeout=5)
    retry_client = handler.create_client(timeout=5, event_hooks=None)
    response = None
    async with server:
        try:
            if mode in ("buffered", "http_error"):
                release.set()
            with (
                patch.object(handler.client, "send", side_effect=initial_error("initial failure")),
                patch.object(handler, "create_client", return_value=retry_client),
            ):
                try:
                    response = await handler.post(
                        f"http://127.0.0.1:{port}/", stream=mode != "buffered"
                    )
                except httpx.HTTPStatusError:
                    assert mode == "http_error"
                    assert retry_client.is_closed
                    return
            assert mode != "http_error"
            if mode == "buffered":
                assert response.content == b"firstlast!"
                assert retry_client.is_closed
                return
            assert not retry_client.is_closed, "Retry client closed before body consumption"
            if mode == "closed":
                await response.aclose()
            elif mode == "cancelled":
                reading = asyncio.create_task(response.aread())
                await asyncio.sleep(0.02)
                reading.cancel()
                try:
                    await reading
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError("Cancellation was swallowed")
            else:
                release.set()
                try:
                    body = await response.aread()
                    assert mode == "complete"
                    assert body == b"firstlast!"
                except httpx.ReadError:
                    assert mode == "broken"
            assert retry_client.is_closed, f"Retry client leaked after {mode}"
            await response.aclose()
        finally:
            release.set()
            if response is not None:
                await response.aclose()
            await retry_client.aclose()
            await handler.close()
            await asyncio.wait_for(finished.wait(), timeout=5)


async def main():
    for mode in ("complete", "buffered", "closed", "cancelled", "broken", "http_error"):
        await asyncio.wait_for(check(mode), timeout=10)
        print(f"PASS retry stream: {mode}", flush=True)
    await asyncio.wait_for(check("complete", httpx.RemoteProtocolError), timeout=10)
    print("PASS retry stream: RemoteProtocolError", flush=True)


asyncio.run(main())
