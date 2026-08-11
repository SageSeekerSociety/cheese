"""One place where a turn's input token buckets become ONE number.

Every supply reports the same four buckets under different names — the SDK's
``ResultMessage.usage`` and the metering proxy use Anthropic's wire names
(``cache_read_input_tokens`` / ``cache_creation_input_tokens``), the hook
forwarder uses its own short ones (``cache_read`` / ``cache_write``) — and
``AgentUsage`` has no cache field, so all of them fold into ``input_tokens``.

Folding is not optional: cache reads are NOT free and they dominate. One
observed document-writing task read 2.9M cached tokens against 141k of fresh
input — 20x, and half the cost. A path that drops them under-reports the turn
by more than it reports.

This module exists because the arithmetic was written three separate times and
the third copy (the sdk backend, ``AgentService``) silently omitted both cache
buckets, so sdk-routed turns showed an order of magnitude less input than they
burned. Three copies, three chances to forget; one function, one.
"""


def fold_input_tokens(fresh: object, cache_read: object, cache_creation: object) -> int:
    """Total input tokens billed for a turn: fresh + cache reads + cache writes.

    Values arrive straight off JSON payloads (``None``, strings and missing keys
    all show up in practice), so each is coerced defensively rather than trusted.
    """
    return _as_int(fresh) + _as_int(cache_read) + _as_int(cache_creation)


def _as_int(value: object) -> int:
    if value is None:
        return 0
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


# The wire names each supply uses for the same four buckets. Keyed by the
# reporter so a new supply declares its dialect here instead of open-coding the
# addition again.
_DIALECTS: dict[str, tuple[str, str, str, str]] = {
    # Anthropic wire shape: SDK ResultMessage.usage, metering-proxy log lines.
    "anthropic": (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ),
    # The hook forwarder's short names (app.domain.agent.hook_events).
    "hook": ("input", "output", "cache_read", "cache_write"),
}


def input_output_tokens(payload: dict, dialect: str = "anthropic") -> tuple[int, int]:
    """``(input_tokens, output_tokens)`` from one supply's usage payload, with
    the cache buckets already folded into the input count."""
    fresh, out, cache_read, cache_creation = _DIALECTS[dialect]
    return (
        fold_input_tokens(
            payload.get(fresh), payload.get(cache_read), payload.get(cache_creation)
        ),
        _as_int(payload.get(out)),
    )
