"""Every supply folds cache tokens the SAME way (资源面板 P1-8).

Three code paths report a turn's tokens — the hook forwarder (machines running
interactive Claude Code), the metering proxy's log (subscription), and the SDK's
ResultMessage (default sdk backend) — and each names the four buckets
differently. Two of them folded ``cache_read`` / ``cache_creation`` into the
input count; the third silently dropped both, so sdk-routed turns reported an
order of magnitude less input than they burned.

These tests drive the three paths with the SAME turn expressed in each one's
dialect and require the same answer. They are about observable behaviour: given
this payload, what does the platform record?
"""

import pytest

from app.domain.agent.hook_events import usage_from_hook
from app.domain.usage.tokens import fold_input_tokens, input_output_tokens

# One turn, as each supply describes it. 2.9M cached against 141k fresh is the
# real observed ratio — dropping the cache buckets here loses 95% of the input.
FRESH, OUT, CACHE_READ, CACHE_WRITE = 141_000, 12_000, 2_900_000, 33_000
EXPECTED_INPUT = FRESH + CACHE_READ + CACHE_WRITE

_ANTHROPIC = {
    "input_tokens": FRESH,
    "output_tokens": OUT,
    "cache_read_input_tokens": CACHE_READ,
    "cache_creation_input_tokens": CACHE_WRITE,
}
_HOOK = {
    "input": FRESH,
    "output": OUT,
    "cache_read": CACHE_READ,
    "cache_write": CACHE_WRITE,
    "model": "claude-opus-5",
}


def test_sdk_result_usage_folds_cache_buckets():
    """The SDK's ResultMessage.usage shape — the path that dropped them."""
    assert input_output_tokens(_ANTHROPIC) == (EXPECTED_INPUT, OUT)


def test_hook_payload_folds_cache_buckets():
    usage = usage_from_hook(_HOOK)
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (EXPECTED_INPUT, OUT)


def test_proxy_line_folds_cache_buckets():
    """The metering proxy writes Anthropic's names into its log line."""
    proxy_line = {**_ANTHROPIC, "total_tokens": EXPECTED_INPUT + OUT, "ts": 1.0}
    assert input_output_tokens(proxy_line) == (EXPECTED_INPUT, OUT)


def test_all_three_paths_agree_on_the_same_turn():
    """The point of the shared helper: no supply may disagree about a turn."""
    sdk_input, sdk_out = input_output_tokens(_ANTHROPIC)
    hook = usage_from_hook(_HOOK)
    assert hook is not None
    proxy_input, proxy_out = input_output_tokens(_ANTHROPIC)
    assert sdk_input == hook.input_tokens == proxy_input == EXPECTED_INPUT
    assert sdk_out == hook.output_tokens == proxy_out == OUT


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"input_tokens": None, "cache_read_input_tokens": None},
        {"input_tokens": "not a number"},
    ],
)
def test_missing_or_junk_buckets_read_as_zero(payload):
    """Usage payloads come off the wire; a missing or malformed bucket must
    never crash a turn's accounting."""
    assert input_output_tokens(payload) == (0, 0)


def test_hook_payload_without_any_tokens_reports_no_usage():
    """No tokens at all = nothing knowable, which is not the same as a zero
    turn — the caller records an unmetered row instead."""
    assert usage_from_hook({"model": "m"}) is None


def test_fold_is_plain_addition():
    assert fold_input_tokens(1, 2, 3) == 6
    assert fold_input_tokens(1, None, "x") == 1
