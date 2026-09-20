"""Resolve optional telemetry during startup without enabling tracing."""

import asyncio
import os

from litellm.integrations.otel import runtime
from litellm.proxy import proxy_server


async def main():
    proxy_server.premium_user = True
    os.environ['LITELLM_MASTER_KEY'] = 'synthetic-master'
    async with proxy_server.proxy_startup_event(proxy_server.app):
        cached = runtime._otel_runtime.cache_info()
        resolved = runtime._otel_runtime()
        assert resolved is not None, 'Pinned image must retain its telemetry SDK'
        with runtime.phase_span('synthetic_auth') as span:
            assert span is None
        runtime.seed_request_identity({}, model='synthetic')
        after_request = runtime._otel_runtime.cache_info()
    assert cached.currsize == 1, 'Telemetry resolution still waits for a request'
    assert after_request.misses == cached.misses
    print('Telemetry resolved before readiness; inactive tracing remains inactive', flush=True)


asyncio.run(main())
