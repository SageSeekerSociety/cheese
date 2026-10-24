"""Turn a recording made at the composer into a message the person can send.

Two calls through the gateway: a speech model hears the audio, with the room's
names as hotwords so 「芝士ni」 is not heard as 「知识你」; then a small model
tidies what was heard the way the person meant to write it — fillers dropped,
a correction kept over what it corrected, a spoken list laid out as one, a
spoken 「艾特某某」 written as ``@某某`` so the composer turns it into a mention.
Both come back, the tidy text and what was heard, so the person can take the
words they actually said instead.

Each call is a row in ``resource_usage`` against the project, under its own
kind; while ``voice_input_charged`` is off those rows cost no credits. The
person's seconds are a ``VoiceInput`` row, which the daily allowance reads.
"""

import io
import logging
import uuid
import wave
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import model_roles
from app.core.config import settings
from app.core.errors import QuotaExceededError, SystemBusyError, ValidationError
from app.core.sentences import say
from app.domain.gateway_chat import Completion, GatewayCallError, GatewayChat
from app.domain.service_keys import KeySpec, service_key
from app.domain.usage.credits import spend_to_credits
from app.domain.usage.ledger import Ledger, payer_for_person, payer_for_project
from app.domain.voice.models import VoiceInput

logger = logging.getLogger(__name__)

ASR_KIND = "voice_asr"
POLISH_KIND = "voice_polish"
#: The day the allowance resets on, the product's day.
_DAY = ZoneInfo("Asia/Shanghai")
#: Words of the product people say aloud and a recogniser has no reason to know.
_TERMS = ("知是", "芝士", "验收卡", "实况文档", "支线", "频道", "任务", "资料库")
#: DashScope's instant hotwords: weights 1-5, at most 2000 entries.
_NAME_WEIGHT = 4
_TERM_WEIGHT = 3
_MAX_WORDS = 2000

POLISH_PROMPT = """你把一段语音转写整理成可以直接发出去的聊天消息。

规则：
- 去掉口水词和无意义的停顿词（嗯、啊、那个、就是、然后呃）。
- 说话人中途改口时只保留最后的说法，例如「周三，不对，周四」写成「周四」。
- 重复说了两遍的话只留一遍。
- 口述的几个并列要点（「第一……第二……」「一是……二是……」）排成编号列表。
- 加上正确的标点，必要时分段。
- 说到「艾特」「at」后面跟着下面名单里的名字时，写成 @名字，名字与名单完全一致。
- 名单里的名字、代码名、文件名、数字、英文词照原样写，不翻译。
- 不添加说话人没说的内容，不回答其中的问题，不加任何解释。
- 语气保持原样，口语就写口语。

只输出整理后的消息正文。"""


@dataclass(frozen=True)
class Heard:
    #: What to put in the composer: the tidy text, or what was heard when
    #: tidying failed.
    text: str
    #: What the speech model heard, word for word.
    raw: str
    seconds: float
    #: Seconds of today's allowance left after this recording.
    remaining_seconds: float


def _key(name: str, model: str, budget_usd: float) -> KeySpec:
    return KeySpec(
        name=f"voice-{name}-gateway-key",
        alias=f"voice-{name}",
        model=model,
        budget_usd=budget_usd,
        rpm=600,
    )


def asr_key_spec() -> KeySpec:
    return _key("asr", model_roles.model("voice_asr_model"), 100.0)


def polish_key_spec() -> KeySpec:
    return _key("polish", model_roles.model("voice_polish_model"), 30.0)


def wav_seconds(audio: bytes) -> float:
    """How long a WAV recording is; refuses anything that is not one."""
    try:
        with wave.open(io.BytesIO(audio)) as w:
            frames, rate = w.getnframes(), w.getframerate()
    except (wave.Error, EOFError) as exc:
        raise ValidationError(say("voiceNotWav")) from exc
    if rate <= 0 or frames <= 0:
        raise ValidationError(say("voiceEmpty"))
    return frames / rate


def vocabulary(names: Iterable[str]) -> dict[str, int]:
    """The hotwords one recording is heard with: the room's names, then the
    product's words."""
    words: dict[str, int] = {}
    for name in names:
        name = name.strip()
        if name and name not in words:
            words[name] = _NAME_WEIGHT
    for term in _TERMS:
        words.setdefault(term, _TERM_WEIGHT)
    return dict(list(words.items())[:_MAX_WORDS])


def context(names: Iterable[str]) -> str:
    listed = "、".join(n for n in dict.fromkeys(n.strip() for n in names) if n)
    return (
        f"知是协作平台的一段聊天。在场的人和 AI 队友：{listed}。"
        if listed
        else "知是协作平台的一段聊天。"
    )


def _today_start(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(_DAY).date(), time(), _DAY).astimezone(UTC)


class VoiceInputs:
    def __init__(
        self,
        session: AsyncSession,
        *,
        asr: GatewayChat | None = None,
        polish: GatewayChat | None = None,
    ) -> None:
        self._session = session
        self._asr = asr
        self._polish = polish

    async def spoken_today(self, user_id: int, now: datetime | None = None) -> float:
        start = _today_start(now or datetime.now(UTC))
        total = await self._session.scalar(
            select(func.coalesce(func.sum(VoiceInput.seconds), 0.0)).where(
                VoiceInput.user_id == user_id, VoiceInput.created_at >= start
            )
        )
        return float(total or 0.0)

    async def _chats(self) -> tuple[GatewayChat, GatewayChat | None]:
        if self._asr is None:
            key = await service_key(self._session, asr_key_spec())
            if key is None:
                raise SystemBusyError(say("voiceUnavailable"))
            self._asr = GatewayChat(
                key, model_roles.model("voice_asr_model"), max_tokens=0
            )
        if self._polish is None:
            key = await service_key(self._session, polish_key_spec())
            if key is not None:
                self._polish = GatewayChat(
                    key, model_roles.model("voice_polish_model"), max_tokens=1024
                )
        return self._asr, self._polish

    async def hear(
        self,
        *,
        user_id: int,
        project_id: uuid.UUID | None,
        topic_id: uuid.UUID | None,
        audio: bytes,
        names: Iterable[str],
    ) -> Heard:
        """Hear a recording said in a project's room, task or 支线, or, with
        no project, to the person's own 芝士, which they pay for themselves."""
        seconds = wav_seconds(audio)
        if seconds > settings.voice_max_seconds + 1:
            raise ValidationError(
                say("voiceTooLong", seconds=settings.voice_max_seconds)
            )
        left = settings.voice_daily_seconds - await self.spoken_today(user_id)
        if seconds > left:
            raise QuotaExceededError(say("voiceDailyUsedUp"))
        names = list(names)
        asr, polish = await self._chats()
        try:
            heard = await asr.transcribe(
                audio=audio,
                media_type="audio/wav",
                context=context(names),
                vocabulary=vocabulary(names),
                timeout=settings.voice_asr_timeout_seconds,
            )
        except GatewayCallError as exc:
            logger.warning("voice transcription failed: %s", exc)
            raise SystemBusyError(say("voiceNotHeard")) from exc
        raw = heard.content.strip()
        await self._record(
            user_id,
            project_id,
            topic_id,
            ASR_KIND,
            model_roles.model("voice_asr_model"),
            heard,
        )
        self._session.add(
            VoiceInput(
                user_id=user_id,
                project_id=project_id,
                conversation_id=topic_id,
                seconds=seconds,
            )
        )
        text = (
            await self._tidy(polish, raw, names, user_id, project_id, topic_id)
            if raw
            else ""
        )
        await self._session.commit()
        return Heard(
            text=text or raw,
            raw=raw,
            seconds=seconds,
            remaining_seconds=max(0.0, left - seconds),
        )

    async def _tidy(
        self,
        polish: GatewayChat | None,
        raw: str,
        names: list[str],
        user_id: int,
        project_id: uuid.UUID | None,
        topic_id: uuid.UUID | None,
    ) -> str:
        """The tidy text, or "" when tidying is unavailable or failed: what was
        heard still reaches the composer."""
        if polish is None:
            return ""
        listed = "、".join(dict.fromkeys(n for n in names if n.strip())) or "（无）"
        try:
            done = await polish.complete(
                system=POLISH_PROMPT,
                prompt=f"名单：{listed}\n\n转写：{raw}",
                timeout=settings.voice_polish_timeout_seconds,
            )
        except GatewayCallError as exc:
            logger.warning("voice polish failed: %s", exc)
            return ""
        await self._record(
            user_id,
            project_id,
            topic_id,
            POLISH_KIND,
            model_roles.model("voice_polish_model"),
            done,
        )
        return done.content.strip()

    async def _record(
        self,
        user_id: int,
        project_id: uuid.UUID | None,
        topic_id: uuid.UUID | None,
        kind: str,
        model: str,
        call: Completion,
    ) -> None:
        ledger = Ledger(self._session)
        await ledger.record(
            await payer_for_person(self._session, user_id)
            if project_id is None
            else await payer_for_project(self._session, project_id),
            credits=spend_to_credits(call.cost_usd)
            if settings.voice_input_charged
            else 0.0,
            model=model,
            input_tokens=call.usage.prompt_tokens,
            output_tokens=call.usage.completion_tokens,
            cost_usd=call.cost_usd,
            route="gateway",
            kind=kind,
            topic_id=topic_id,
        )
