"""Exercise disabled and enabled logging through the actual gateway router."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from litellm.router import Router, verbose_router_logger


class Payload:
    def __init__(self):
        self.conversions = 0

    def __repr__(self):
        self.conversions += 1
        return 'synthetic-payload'


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


async def main():
    capture = Capture()
    result = object()
    fallback = AsyncMock(return_value=result)
    router = SimpleNamespace(
        _ageneric_api_call_with_fallbacks_helper=None,
        _update_kwargs_before_fallbacks=lambda **kwargs: None,
        async_function_with_fallbacks=fallback,
    )
    with patch.object(verbose_router_logger, 'handlers', [capture]), patch.object(
        verbose_router_logger, 'propagate', False
    ):
        for level in [logging.INFO, logging.DEBUG]:
            verbose_router_logger.setLevel(level)
            payload = Payload()
            response = await Router._ageneric_api_call_with_fallbacks(
                router, model='synthetic-model', original_function=None,
                messages=payload,
            )
            assert response is result
            assert fallback.call_args.kwargs['messages'] is payload
            assert payload.conversions == (1 if level == logging.DEBUG else 0)
    assert fallback.await_count == 2
    assert capture.messages == [
        "Inside ageneric_api_call_with_fallbacks() - model: synthetic-model; kwargs: "
        "{'messages': synthetic-payload, 'model': 'synthetic-model', "
        "'original_generic_function': None, 'original_function': None}"
    ]
    print('Router logging: disabled payload conversion skipped; enabled output and dispatch passed')


if __name__ == '__main__':
    asyncio.run(main())
