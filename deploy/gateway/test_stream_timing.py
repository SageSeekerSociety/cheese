"""Check streamed logging without contacting a model provider."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from litellm.llms.anthropic.experimental_pass_through.messages.streaming_iterator import (
    BaseAnthropicMessagesStreamingIterator,
)


async def main():
    request_start = datetime.now() - timedelta(seconds=2)
    logger = SimpleNamespace(start_time=request_start, model_call_details={})
    iterator = BaseAnthropicMessagesStreamingIterator(logger, {})
    first_chunk = datetime.now()
    iterator.completion_start_time = first_chunk
    callback = AsyncMock()
    queued = []
    with patch(
        "litellm.proxy.pass_through_endpoints.streaming_handler."
        "PassThroughStreamingHandler._route_streaming_logging_to_handler",
        callback,
    ), patch(
        "litellm.llms.anthropic.experimental_pass_through.messages.streaming_iterator."
        "GLOBAL_LOGGING_WORKER.ensure_initialized_and_enqueue",
        lambda async_coroutine: queued.append(async_coroutine),
    ):
        await iterator._handle_streaming_logging([])
    # The logging call is handed to the logging worker's queue, not run inline.
    assert len(queued) == 1
    await queued[0]
    callback.assert_awaited_once()
    values = callback.call_args.kwargs
    assert values["start_time"] == request_start
    assert values["end_time"] >= first_chunk
    assert logger.model_call_details["completion_start_time"] == first_chunk
    print("Stream logging preserves request start and first-chunk timestamps")


if __name__ == "__main__":
    asyncio.run(main())
