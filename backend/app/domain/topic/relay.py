"""母子传话 (父话题 ↔ 直接子话题): an explicit channel that actually WAKES the
other side.

Why this exists as its own thing rather than "just post a comment there".
`POST /topics/{id}/comments` looks like the obvious channel and is not one: it
summons only when the commenter is a HUMAN (`if not actor.is_agent`), so a
parent room's 芝士 writing into its sub-topic landed a row in the database and
woke nobody. Worse, that route is not in `app.main._CHEESE_WRITE_PATHS`, so an
agent calling it never passes the per-turn token gate at all — the symptom of a
missing entry is silent admission, not 401. Both halves of that were invisible
from the calling side: the write succeeded.

Two rules this module exists to enforce:

**Direction.** Only 父 → 直接子 and 子 → 父. Not "any topic to any topic" — the
platform's isolation unit is the topic, and a general topic-to-topic mailbox
hands every 分身 a way to reach every other one. The parent/child edge is the one
relationship that is already a supervision relationship, so it is the only one
that gets a channel. Note that the per-turn token gate CANNOT enforce this: a
project-scoped agent credential opens the gate for every topic of its project
(see `is_valid_cheese_token`). The direction check here is the boundary, and the
gate entry only proves the caller is *some* agent of this project.

**Speed.** 传话要**快**，不是"下一轮再说". Three tiers, in this order:

1. 对方正在跑一轮 → 直接插进那一轮 (`ChatService.merge_into_running_turn`, seconds).
   This is the same path a human's mid-turn message takes; its own comment says
   it "gets the message in front of 芝士 in seconds". Only the hooks-driven
   backends own a live screen, so it answers False elsewhere and we fall through.
2. 对方闲着 → 起一轮 (`AgentWorkRunner.submit`).
3. 已经有一个还没跑起来的唤醒在排队 → 这条搭同一轮，不重复叫醒 (`WakeCoalescer`).

Tier 3 is why a plain "skip if busy" is wrong: a relay that lands after a turn
built its prompt would be dropped outright, so what cannot be injected must
either start a turn or be buffered onto one — never neither.
"""

import uuid
from collections.abc import Callable

from app.core.errors import NotFoundError, ValidationError
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.services import TopicMemberService

#: 一条传话的长度上限。传话是「追加一条要求」，不是搬运一篇文档 —— 长的东西
#: 属于实况文档，那边两侧都读得到。
MAX_RELAY_CHARS = 4000

#: `target` 的别名：子话题不用知道母话题叫什么就能回话。
PARENT_ALIASES = frozenset({"parent", "母话题", "父话题", "上级", "up"})


class RelayDirection:
    """Which way a relay goes, from the SENDER's point of view."""

    TO_CHILD = "to_child"
    TO_PARENT = "to_parent"


class RelayDelivery:
    """How the message reached the other side — the sender is told which.

    They are not interchangeable: ``injected`` means 芝士 has it NOW, ``woke``
    means in a moment, ``merged`` means it rides a turn that has not started, and
    ``archived`` means nobody will ever read it. Collapsing these into a bare
    "sent" is how a sender ends up believing a message was acted on.
    """

    INJECTED = "injected"
    WOKE = "woke"
    MERGED = "merged"
    ARCHIVED = "archived"


def _title_matches(topic: Topic, needle: str) -> bool:
    return topic.title.strip() == needle or needle in topic.title


def _strip_ref_token(raw: str) -> str:
    """``<#uuid>`` (the platform's topic-reference token) → ``uuid``.

    芝士 sees topics as ``@标题`` in prose and as ``<#id>`` once canonicalized, so
    both spellings reach the CLI. Accepting the token here means the agent never
    has to know which form it is holding.
    """
    text = raw.strip()
    if text.startswith("<#") and text.endswith(">"):
        return text[2:-1].strip()
    return text


class TopicRelayService:
    """Write one topic's message into the topic on the other end of the
    parent/child edge, and report who should be woken."""

    def __init__(self, session) -> None:
        self._session = session
        self._topics = TopicRepository(session)
        self._blocks = BlockRepository(session)
        self._members = TopicMemberService(session)

    async def resolve_target(self, *, sender: Topic, target: str) -> Topic:
        """The topic ``target`` names, restricted to sender's parent + children.

        Resolution is deliberately scoped to the reachable set rather than "look
        this title up in the project": a lookup over every topic would answer
        "does a topic called X exist" to a caller that may not reach X, and the
        friendly `@标题` form is only useful for the handful of topics on this
        edge anyway.
        """
        wanted = _strip_ref_token(target)
        if not wanted:
            raise ValidationError("要发给谁？给一个子话题（或母话题）的 id 或标题")
        candidates: list[Topic] = list(await self._topics.list_children(sender.id))
        parent: Topic | None = None
        if sender.parent_id is not None:
            parent = await self._topics.get(sender.parent_id)
            if parent is not None:
                candidates.append(parent)
        # 回话不该要求先查到母话题叫什么：`cheese tell parent "..."` 就够了。
        if wanted.lower() in PARENT_ALIASES or wanted in PARENT_ALIASES:
            if parent is None:
                raise ValidationError("这个话题没有母话题")
            return parent
        try:
            wanted_id = uuid.UUID(wanted)
        except ValueError:
            wanted_id = None
        if wanted_id is not None:
            for topic in candidates:
                if topic.id == wanted_id:
                    return topic
            # A real id that is not on this edge is the interesting failure: say
            # WHY, or the caller retries the same call believing it mistyped.
            if await self._topics.get(wanted_id) is not None:
                raise ValidationError(
                    "传话只能发给你的直接子话题或你的母话题。"
                    "这个话题跟你没有父子关系，发不过去。"
                )
            raise NotFoundError("没有这个话题")
        exact = [t for t in candidates if t.title.strip() == wanted]
        loose = exact or [t for t in candidates if _title_matches(t, wanted)]
        if not loose:
            raise ValidationError(
                f"你的子话题/母话题里没有叫「{wanted}」的。"
                "可选的是：" + ("、".join(t.title for t in candidates) or "（没有）")
            )
        if len(loose) > 1:
            raise ValidationError(
                f"「{wanted}」对上了多个话题（{'、'.join(t.title for t in loose)}），"
                "用 id 指明是哪个"
            )
        return loose[0]

    @staticmethod
    def direction(*, sender: Topic, target: Topic) -> str:
        """Reject anything that is not the parent/child edge."""
        if target.parent_id == sender.id:
            return RelayDirection.TO_CHILD
        if sender.parent_id == target.id:
            return RelayDirection.TO_PARENT
        raise ValidationError(
            "传话只能发给你的直接子话题或你的母话题。"
            "两个没有父子关系的话题之间不通 —— 那等于放弃话题级隔离。"
        )

    async def relay(
        self, *, sender: Topic, target: Topic, content: str
    ) -> tuple[Block, str]:
        """Land the message in ``target``'s timeline. Returns (block, direction).

        The block is authored by the RECEIVING room's 芝士, the same way a
        returned conclusion is (`TopicService.return_conclusion`): a message from
        someone who is not in the room reads as a ghost, and the sending room's
        分身 is not on the receiver's roster. `refs` carries the sender so the
        chip links back.
        """
        text = content.strip()
        if not text:
            raise ValidationError("传话内容不能为空")
        if len(text) > MAX_RELAY_CHARS:
            raise ValidationError(
                f"传话内容太长（{len(text)} 字符，上限 {MAX_RELAY_CHARS}）。"
                "长的东西写进实况文档，那边读得到。"
            )
        direction = self.direction(sender=sender, target=target)
        label = "母话题追加" if direction == RelayDirection.TO_CHILD else "子话题来信"
        author = await self._members.resolve_agent_handle(target.id)
        block = await self._blocks.add(
            project_id=target.project_id,
            topic_id=target.id,
            author=author,
            author_type=AuthorType.ai,
            content=f"【{label}｜{sender.title}】\n{text}",
            kind=BlockKind.message,
            refs=[str(sender.id)],
        )
        return block, direction


def inline_line(*, direction: str, sender_title: str, message: str) -> str:
    """The one line injected into a turn that is ALREADY running.

    It has to be short — it lands mid-thought, next to whatever 芝士 is doing —
    and it has to say where it came from and how much authority it carries, or an
    agent mid-task reads it as an aside and finishes the wrong thing.
    """
    if direction == RelayDirection.TO_CHILD:
        return (
            f"母话题「{sender_title}」追加了一条要求（**跟简报冲突时以这条为准**）："
            f"{message}"
        )
    return f"子话题「{sender_title}」来信：{message}"


def relay_prompt(
    *, direction: str, sender_title: str, sender_id: uuid.UUID, messages: list[str]
) -> str:
    """What the woken 芝士 is asked to do with the relayed text.

    ``messages`` may hold more than one line — that is the coalesced case, and
    the count is stated rather than hidden: "三条消息合成一轮" and "两条被吞了"
    look identical to the receiver otherwise.
    """
    body = "\n\n---\n\n".join(messages)
    extra = (
        f"（期间一共来了 {len(messages)} 条，合成这一轮一起处理）\n"
        if len(messages) > 1
        else ""
    )
    if direction == RelayDirection.TO_CHILD:
        return (
            f"母话题「{sender_title}」给你追加了要求/说明。{extra}原话：\n"
            f"{body}\n\n"
            "任务简报是一次性的、写完就改不了，所以这条是后来补上的："
            "**跟简报冲突时以这条为准**。先判断它跟你手上的活是什么关系 —— "
            "要改方向就改，范围/前提不清楚就用 `cheese ask` 问回去，"
            "只是补充信息就记下来接着做。别当成一个新任务从头开始。"
            f"要回话用 `cheese tell '<#{sender_id}>' \"...\"`。"
        )
    return (
        f"子话题「{sender_title}」给你发来一条消息。{extra}原话：\n"
        f"{body}\n\n"
        "这不是结论回流，没有结论卡要结算 —— 它要么是在问你，要么是在报一个"
        "中途发现。该拍板就拍板，该回话就用 "
        f"`cheese tell '<#{sender_id}>' \"...\"` 回过去。"
    )


class WakeCoalescer:
    """One wake-up per target topic at a time.

    The problem: three relays in a row must cost the receiver ONE turn. Turns on
    a topic serialize on ChatService's per-topic lock, so three `submit`s would
    all run — three model turns for what is one instruction.

    The problem's other half is why this is not simply "skip if a turn is
    running": a relay that lands mid-turn may arrive AFTER that turn built its
    prompt, so dropping it loses the message outright. (The platform's own
    merge-into-running-turn path does not help here — it only covers blocks that
    are human input, and a relay is written by an agent.)

    So: the first relay wakes immediately and takes ownership of the topic;
    everything that arrives while that turn runs is buffered; when the turn ends,
    the whole buffer goes out as exactly one follow-up turn. Bounded at one extra
    turn per burst, and nothing is silently dropped.

    Process-scoped, like the runner's own queues. A restart drops the buffer —
    the messages themselves are durable blocks in the room, but a buffered
    wake-up that never fires is a real (accepted) hole; see the CLI's note.
    """

    def __init__(self) -> None:
        self._pending: dict[uuid.UUID, list[str]] = {}
        self._inflight: set[uuid.UUID] = set()

    def offer(self, topic_id: uuid.UUID, message: str) -> list[str] | None:
        """Register one relay. Returns the batch to wake with NOW, or None when a
        wake already owns this topic (the message rides that one's follow-up)."""
        if topic_id in self._inflight:
            self._pending.setdefault(topic_id, []).append(message)
            return None
        self._inflight.add(topic_id)
        return [message]

    def release(self, topic_id: uuid.UUID) -> list[str] | None:
        """Call when the woken turn ENDS. Returns the buffered batch that now
        needs its own turn (ownership stays), or None (ownership released)."""
        batch = self._pending.pop(topic_id, None)
        if batch:
            return batch
        self._inflight.discard(topic_id)
        return None

    def buffered(self, topic_id: uuid.UUID) -> int:
        """How many relays are waiting on the current turn to end. Diagnostics."""
        return len(self._pending.get(topic_id, ()))


#: Process-scoped: the coalescing window is "while a turn runs", which is a
#: property of this process's event loop, not of the database.
wake_queue = WakeCoalescer()


async def deliver_or_wake(
    *,
    chat,
    runner,
    target: Topic,
    direction: str,
    sender_title: str,
    sender_id: uuid.UUID,
    block_id: uuid.UUID,
    message: str,
    coalescer: WakeCoalescer | None = None,
    submit: Callable[..., object] | None = None,
) -> str:
    """Get the relay in front of the receiving 芝士 the fastest way available.

    Returns a `RelayDelivery` value. The three live tiers are tried in order:
    inject into the running turn → start a turn → ride the wake already queued.
    """
    if target.status == TopicStatus.archived:
        return RelayDelivery.ARCHIVED
    # Tier 1: 对方正在跑 → 插进去，秒级。False everywhere the transport has no
    # live screen (SDK/per-turn providers), which is not a failure — it is the
    # pre-existing "run a turn" behaviour.
    injected = await chat.merge_into_running_turn(
        target.id,
        [block_id],
        inline_line(direction=direction, sender_title=sender_title, message=message),
        _relay_author(direction),
    )
    if injected:
        return RelayDelivery.INJECTED
    woke = wake_target(
        runner=runner,
        chat=chat,
        target=target,
        direction=direction,
        sender_title=sender_title,
        sender_id=sender_id,
        message=message,
        coalescer=coalescer,
        submit=submit,
    )
    return RelayDelivery.WOKE if woke else RelayDelivery.MERGED


def _relay_author(direction: str) -> str:
    """Who the injected line is attributed to on the receiving screen."""
    return "母话题" if direction == RelayDirection.TO_CHILD else "子话题"


def wake_target(
    *,
    runner,
    chat,
    target: Topic,
    direction: str,
    sender_title: str,
    sender_id: uuid.UUID,
    message: str,
    coalescer: WakeCoalescer | None = None,
    submit: Callable[..., object] | None = None,
) -> bool:
    """Wake ``target`` for this relay unless a wake already owns it.

    Returns True when a turn was started now. False means either the topic is
    archived (nothing to wake) or the message was folded into a wake already in
    flight — both are success from the sender's side; the response says which.
    """
    if target.status == TopicStatus.archived:
        return False
    queue = coalescer if coalescer is not None else wake_queue
    batch = queue.offer(target.id, message)
    if batch is None:
        return False
    _submit_wake(
        runner=runner,
        chat=chat,
        target_id=target.id,
        direction=direction,
        sender_title=sender_title,
        sender_id=sender_id,
        batch=batch,
        queue=queue,
        submit=submit,
    )
    return True


def _submit_wake(
    *,
    runner,
    chat,
    target_id: uuid.UUID,
    direction: str,
    sender_title: str,
    sender_id: uuid.UUID,
    batch: list[str],
    queue: WakeCoalescer,
    submit: Callable[..., object] | None,
) -> None:
    def _drain() -> None:
        """The woken turn ended: send whatever piled up behind it, as one turn."""
        follow_up = queue.release(target_id)
        if not follow_up:
            return
        _submit_wake(
            runner=runner,
            chat=chat,
            target_id=target_id,
            direction=direction,
            sender_title=sender_title,
            sender_id=sender_id,
            batch=follow_up,
            queue=queue,
            submit=submit,
        )

    line = "母话题追加了要求" if direction == RelayDirection.TO_CHILD else "子话题来信"
    do_submit = submit if submit is not None else runner.submit
    do_submit(
        chat,
        target_id,
        author="system",
        content=relay_prompt(
            direction=direction,
            sender_title=sender_title,
            sender_id=sender_id,
            messages=batch,
        ),
        summon=True,
        nudge_event=f"📨 {line}（来自「{sender_title}」）",
        on_done=_drain,
    )


__all__ = [
    "MAX_RELAY_CHARS",
    "PARENT_ALIASES",
    "RelayDelivery",
    "RelayDirection",
    "TopicRelayService",
    "WakeCoalescer",
    "deliver_or_wake",
    "inline_line",
    "relay_prompt",
    "wake_queue",
    "wake_target",
]
