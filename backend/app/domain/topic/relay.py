"""房间给它的一条活留话 (`cheese tell`).

One direction, and it is the only one there can be: 房间 → 它派出的活.

A thread is a 分身 running inside its room's own session, so the two things this
module used to do have gone in opposite directions.

**Waking is gone.** There is nothing to wake. The worker is in the room's
session and the room reaches it with its own agent tooling — the platform waking
a place for it would mean raising a container for that place, which is the shape
threads stopped having. So a relay lands as a block and that is the whole of it.

**What lands is the point.** The room talks to its worker in a channel nobody
else can see; this is how what it told it shows up WHERE THE WORK IS. The person
watching that thread — who cannot reach the worker at all — reads the answer
there, and so does whoever opens the thread next week.

The reverse leg (支线 → 它所在的房间) is gone with the sessions: it existed
because a thread's own 芝士 held a per-turn token and had no other way home. A
thread has no turn and no token now. What a worker finishes with reaches the room
as its own result, and the room files it with `cheese conclude-task`.

Why this is not `POST /topics/{id}/comments`: that route summons only when the
commenter is a HUMAN (`if not actor.is_agent`), and it is not in
`app.main._CHEESE_WRITE_PATHS`, so an agent calling it never passes the per-turn
token gate — the symptom of a missing entry is silent admission, not 401. Both
halves were invisible from the calling side: the write succeeded.
"""

import uuid

from app.core.errors import NotFoundError, ValidationError
from app.domain.block.models import Block
from app.domain.room_task.place import Place, PlaceResolver
from app.domain.room_task.services import TaskService
from app.domain.topic.services import TopicService

#: 一条留话的长度上限。留话是「追加一条要求」，不是搬运一篇文档 —— 长的东西
#: 属于实况文档，那边两侧都读得到。
MAX_RELAY_CHARS = 4000


def _title_matches(place: Place, needle: str) -> bool:
    return place.title.strip() == needle or needle in place.title


def _strip_ref_token(raw: str) -> str:
    """``<#uuid>`` (the platform's topic-reference token) → ``uuid``.

    芝士 sees places as ``@标题`` in prose and as ``<#id>`` once canonicalized, so
    both spellings reach the CLI. Accepting the token here means the agent never
    has to know which form it is holding.
    """
    text = raw.strip()
    if text.startswith("<#") and text.endswith(">"):
        return text[2:-1].strip()
    return text


class TopicRelayService:
    """Write one message from a room onto one of the threads it dispatched."""

    def __init__(self, session) -> None:
        self._session = session
        self._places = PlaceResolver(session)
        self._tasks = TaskService(session)

    async def _reachable(self, sender: Place) -> list[Place]:
        """The threads *sender* dispatched — everywhere it may write.

        The set is small on purpose: a lookup over every place would answer
        "does a place called X exist" to a caller that may not reach X.
        """
        threads = await self._tasks.threads_for_room(sender.room_id, limit=0)
        return [Place(room=sender.room, task=t) for t, _ in threads]

    async def resolve_target(self, *, sender: Place, target: str) -> Place:
        """The thread ``target`` names, restricted to what *sender* dispatched."""
        if sender.task is not None:
            raise ValidationError(
                "留话是房间对它派出的活说的。一条活没有自己的会话，"
                "它要说什么就是做它的那个分身自己说。"
            )
        wanted = _strip_ref_token(target)
        if not wanted:
            raise ValidationError("要发给谁？给这个房间里一条活的 id 或标题")
        candidates = await self._reachable(sender)
        try:
            wanted_id = uuid.UUID(wanted)
        except ValueError:
            wanted_id = None
        if wanted_id is not None:
            for place in candidates:
                if place.id == wanted_id:
                    return place
            # A real id that is not on this edge is the interesting failure: say
            # WHY, or the caller retries the same call believing it mistyped.
            if await self._places.resolve(wanted_id) is not None:
                raise ValidationError(
                    "留话只能发给你自己派出的活。这个地点不归你，发不过去。"
                )
            raise NotFoundError("没有这个地点")
        exact = [t for t in candidates if t.title.strip() == wanted]
        loose = exact or [t for t in candidates if _title_matches(t, wanted)]
        if not loose:
            raise ValidationError(
                f"你派出的活里没有叫「{wanted}」的。"
                "可选的是：" + ("、".join(t.title for t in candidates) or "（没有）")
            )
        if len(loose) > 1:
            raise ValidationError(
                f"「{wanted}」对上了多个地点（{'、'.join(t.title for t in loose)}），"
                "用 id 指明是哪个"
            )
        return loose[0]

    async def relay(self, *, sender: Place, target: Place, content: str) -> Block:
        """Land the message on ``target``'s timeline.

        The block itself is written by `TopicService.add_relay_block` (authorship
        rule and the reason it lives there are documented on that method).
        """
        text = content.strip()
        if not text:
            raise ValidationError("留话内容不能为空")
        if len(text) > MAX_RELAY_CHARS:
            raise ValidationError(
                f"留话内容太长（{len(text)} 字符，上限 {MAX_RELAY_CHARS}）。"
                "长的东西写进实况文档，那边读得到。"
            )
        if target.task is None or target.room_id != sender.room_id:
            raise ValidationError("留话只能发给你自己派出的活。")
        return await TopicService(self._session).add_relay_block(
            target=target, sender=sender, label="房间追加", text=text
        )


__all__ = [
    "MAX_RELAY_CHARS",
    "TopicRelayService",
]
