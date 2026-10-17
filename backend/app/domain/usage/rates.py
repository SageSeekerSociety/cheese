"""USD per token for a model a call will use, and what that call costs at them.

The rates come from the gateway's own rate table (``pricing.model_rates``) —
one copy of the truth rather than a second one written here — and are read into
``Rates``, the shape the ledger charges a call with (``ledger.Ledger.
charge_priced``) and the routes price an assistant turn with.
"""

from collections.abc import Mapping
from dataclasses import dataclass

#: One model's row in the gateway's rate table: input, output, cache read,
#: five-minute cache write and, when the gateway names it, one-hour cache write.
RateRow = tuple[float, float, float, float, float | None]


@dataclass(frozen=True)
class Rates:
    """USD per token for the model a call will use: fresh prompt tokens, output
    tokens, prompt tokens read from the provider's cache, prompt tokens written
    to its five-minute cache, and to its one-hour cache (the five-minute rate
    when the table names none)."""

    input: float
    output: float
    cache_read: float
    cache_write: float
    cache_write_1h: float | None = None

    def cost_usd(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cache_write_1h_tokens: int = 0,
    ) -> float:
        """What a call costs at these rates, billed the way the gateway bills
        it: ``input_tokens`` counts every prompt token, cached ones included,
        and the cached shares are billed at their own rates.
        ``cache_write_1h_tokens`` is the share of ``cache_write_tokens`` that
        went to the one-hour cache."""
        fresh = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
        hour = min(cache_write_1h_tokens, cache_write_tokens)
        hour_rate = (
            self.cache_write if self.cache_write_1h is None else self.cache_write_1h
        )
        return (
            fresh * self.input
            + cache_read_tokens * self.cache_read
            + (cache_write_tokens - hour) * self.cache_write
            + hour * hour_rate
            + output_tokens * self.output
        )

    @classmethod
    def of(cls, model: str, table: Mapping[str, RateRow] | None) -> "Rates | None":
        """``model``'s rates from the gateway's rate table
        (``usage.pricing.model_rates``); None when a call to it cannot
        be charged."""
        if not table or model not in table:
            return None
        return cls(*table[model])
