"""Malformed provider frames must not interrupt forwarding or later usage."""

import importlib.util
from pathlib import Path

import pytest

_path = (
    Path(__file__).resolve().parents[3] / "deploy/metering-proxy/cheese_billing_core.py"
)
_spec = importlib.util.spec_from_file_location("provider_stream_core", _path)
assert _spec and _spec.loader
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


@pytest.mark.parametrize(
    "fault",
    [
        b"null",
        b"[]",
        b"42",
        b'"unexpected"',
        b'{"message":"unexpected"}',
        b'{"message":[1]}',
        b'{"message":{"model":[],"usage":null}}',
        b'{"type":"message_delta","usage":[1]}',
        b'{"invalid":"\xff"}',
        b'{"truncated":',
    ],
)
@pytest.mark.parametrize("chunk_size", [1, 7, 65536])
def test_bad_provider_event_preserves_later_usage(fault, chunk_size):
    # Synthetic fault injection, not a recording of a provider response.
    stream = (
        b"data: " + fault + b"\n\n"
        b'data: {"type":"message_start","message":{"model":"fixture-model",'
        b'"usage":{"input_tokens":7,"cache_read_input_tokens":3}}}\n\n'
        b'data: {"type":"message_delta","usage":{"output_tokens":11}}\n\n'
        b'data: {"type":"message_stop"}\n\n'
    )
    extractor = core.StreamingUsageExtractor()
    for offset in range(0, len(stream), chunk_size):
        extractor.feed(stream[offset : offset + chunk_size])
    extractor.close()
    assert extractor.model == "fixture-model"
    assert extractor.usage == {
        "input_tokens": 7,
        "cache_read_input_tokens": 3,
        "output_tokens": 11,
    }


def test_invalid_usage_values_do_not_replace_known_counts():
    extractor = core.StreamingUsageExtractor()
    extractor.feed(b'data: {"usage":{"input_tokens":7,"output_tokens":11}}\n')
    extractor.feed(b'data: {"usage":{"input_tokens":true,"output_tokens":-1}}\n')
    extractor.close()
    assert extractor.usage == {"input_tokens": 7, "output_tokens": 11}
