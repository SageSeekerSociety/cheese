from dataclasses import dataclass
from datetime import datetime

from app.core.config import settings
from app.domain.llm.repositories import AIUserQuotaRepository


class QuotaExceededError(Exception):
    """Raised when a user has exhausted their AI quota."""


@dataclass
class QuotaInfo:
    remaining: float
    total: float
    reset_time: datetime


class AiAdviceService:
    def __init__(
        self,
        repo: AIUserQuotaRepository,
        daily_quota: float | None = None,
    ) -> None:
        self._repo = repo
        self._daily_quota = (
            daily_quota if daily_quota is not None else settings.ai_daily_quota
        )

    async def get_quota(self, *, user_id: int) -> QuotaInfo:
        remaining, reset_at = await self._repo.get_quota(
            user_id=user_id, daily_total=self._daily_quota
        )
        return QuotaInfo(
            remaining=remaining, total=self._daily_quota, reset_time=reset_at
        )

    async def check_quota(self, *, user_id: int, amount: float = 1.0) -> bool:
        """Check if user has enough quota without consuming it."""
        remaining, _ = await self._repo.get_quota(
            user_id=user_id, daily_total=self._daily_quota
        )
        return remaining >= amount

    async def consume_quota(self, *, user_id: int, amount: float = 1.0) -> QuotaInfo:
        """Consume quota and return updated quota info."""
        try:
            remaining, reset_at = await self._repo.consume(
                user_id=user_id, amount=amount, daily_total=self._daily_quota
            )
            if remaining < 0:
                raise QuotaExceededError("AI quota exhausted")
            return QuotaInfo(
                remaining=remaining, total=self._daily_quota, reset_time=reset_at
            )
        except ValueError as exc:
            raise QuotaExceededError(str(exc)) from exc

    async def consume_tokens(self, *, user_id: int, tokens: int) -> QuotaInfo:
        """Consume quota based on token count (1 SEU = ~1000 tokens)."""
        seu_consumed = tokens / 1000.0
        return await self.consume_quota(user_id=user_id, amount=seu_consumed)

    async def pre_check_and_reserve(
        self, *, user_id: int, estimated_tokens: int = 1000
    ) -> bool:
        """Pre-check quota before making an LLM call. Returns True if quota is available."""  # noqa: E501
        estimated_seu = estimated_tokens / 1000.0
        return await self.check_quota(user_id=user_id, amount=estimated_seu)
