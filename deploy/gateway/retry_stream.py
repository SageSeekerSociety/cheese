"""Keep a retry's HTTP client alive until its response stream closes."""

import httpx


class ClientOwnedStream(httpx.AsyncByteStream):
    def __init__(self, stream, client):
        self.stream = stream
        self.client = client
        self.closed = False

    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                yield chunk
        finally:
            await self.aclose()

    async def aclose(self):
        if self.closed:
            return
        self.closed = True
        try:
            await self.stream.aclose()
        finally:
            await self.client.aclose()
