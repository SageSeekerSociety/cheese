"""Compute-credit conversion + platform copy (spec §9.1 机构提供算力).

Credits are the institution-facing unit; tokens are what turns actually burn.
The conversion rate lives in settings (compute_credit_tokens, default 1 credit
= 10k tokens) so a deployment can re-price without code changes.

The exhaustion message is PLATFORM copy posted as a structured system event —
never words put in 芝士's mouth (CLAUDE.md 硬性禁止 #4).
"""

from app.core.config import settings

# Posted into the topic 现场 when a turn is refused for lack of credits.
CREDITS_EXHAUSTED_EVENT = (
    "⛔ 项目的算力额度已用完，这轮没有执行。"
    "请联系发放额度的机构续充，或解绑机构任务后自治运行。"
)


def tokens_to_credits(total_tokens: int) -> float:
    """Fold a turn's token usage into compute credits."""
    rate = max(1, settings.compute_credit_tokens)
    return total_tokens / rate
