import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.llm.models import Base


logger = logging.getLogger(__name__)


class LLMCallLog(Base):
    __tablename__ = "llm_call_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_seu: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_type: Mapped[str] = mapped_column(String(32), nullable=False, default="chat")
    context_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    context_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


@dataclass
class LLMCallInfo:
    user_id: int
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    request_type: str = "chat"
    context_type: str | None = None
    context_id: int | None = None
    error: str | None = None


class LLMCallLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log_call(self, info: LLMCallInfo) -> LLMCallLog:
        cost_seu = info.total_tokens / 1000.0
        log = LLMCallLog(
            user_id=info.user_id,
            model=info.model,
            prompt_tokens=info.prompt_tokens,
            completion_tokens=info.completion_tokens,
            total_tokens=info.total_tokens,
            cost_seu=cost_seu,
            latency_ms=info.latency_ms,
            request_type=info.request_type,
            context_type=info.context_type,
            context_id=info.context_id,
            error=info.error,
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(log)
        await self._session.flush()

        logger.info(
            "LLM call: user=%d model=%s tokens=%d cost=%.3f seu latency=%dms",
            info.user_id,
            info.model,
            info.total_tokens,
            cost_seu,
            info.latency_ms,
        )
        return log
