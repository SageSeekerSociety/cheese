"""The text this turn is handed, and the replay count over it.

The prompt is built HERE, not where the backlog was read: an attachment line
has to describe how the image reaches 芝士 on THIS backend, and that is only
knowable once the provider is picked. `getattr` default True: a provider from
outside this repo that never declared the capability keeps the old wording
rather than being told, wrongly, that it drops images.

The body, with its reasoning, moved here verbatim from `preparation`.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.domain.agent.harness.prompt import platform_prompt, prompt_line
from app.domain.agent.prompt import _platform_preamble, _replay_notice
from app.domain.agent.queries import require_pinned_seat
from app.domain.block.output_effects import count_prompt_attempt
from app.domain.block.queries import message_reply_target

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.domain.agent.room.sessions import RoomSessions
    from app.domain.block.models import Block
    from app.domain.room_task.place import Place


async def build_turn_prompt(
    session: AsyncSession,
    *,
    place: Place,
    turn_id: uuid.UUID,
    content: str,
    platform_turn: bool,
    pending: list[Block],
    prompt_pending_ids: list[uuid.UUID],
    notices: list[Block],
    provider: RoomSessions,
    recipient_handle: str,
    recipient_instance_id: uuid.UUID | None,
    gone_files: set[uuid.UUID],
    undecodable_files: set[uuid.UUID],
    credits_note: str | None = None,
) -> tuple[str, str | None]:
    """The prompt text this turn is sent, and the line saying how many times
    this same batch has been attempted. Commits the attempt counter before it
    returns — a counter rolled back silently is the failure it exists to show,
    and nothing later in this session commits."""
    embeds_images = getattr(provider, "embeds_images", True)
    # A reply carries the message it answers: that message is seldom in
    # the backlog (an earlier turn read it), and 「改一下这条」 without
    # 「这条」 is a request 芝士 cannot act on. One already in the
    # backlog is quoted there, so it is not quoted twice.
    replied = {
        b.id: parent
        for b in pending
        if b.reply_to is not None
        and b.reply_to not in prompt_pending_ids
        and (parent := await message_reply_target(session, b.reply_to)) is not None
    }
    backlog = "\n".join(
        prompt_line(
            b,
            embeds_images=embeds_images,
            replied=replied.get(b.id),
            recipient=recipient_handle,
            recipient_instance_id=recipient_instance_id,
            gone=b.id in gone_files,
            undecodable=b.id in undecodable_files,
        )
        for b in pending
    )
    # No pending human block ⇒ nobody spoke: this is a resume nudge
    # or a returned conclusion. Say so, rather than handing
    # 芝士 bare text that looks like a person's message.
    prompt_text = backlog or platform_prompt(content)
    # 平台指令不会被待读消息挤掉。A platform turn EXISTS because of its
    # instruction — raise a worker for this thread, relay this returned
    # card — and nobody re-sends it: the backlog is marked consumed by
    # this same turn, so an instruction dropped here is gone for good and
    # the thread never gets a worker. Dropping it was easy to miss
    # because it needs a room where somebody typed without summoning
    # 芝士, which is a room's ordinary state (没 @ 不等于没说) rather than
    # a rare race.
    if platform_turn and backlog:
        prompt_text = f"{backlog}\n\n{platform_prompt(content)}"
    # 先背景，再这一轮要做的事。What moved under the session is the frame
    # the rest of the prompt has to be read in — a request to revise the
    # 验收标准 means something different once you know that section moved
    # ten minutes ago. One marker over all of them: the marker claims
    # institutional authority, and repeating it per line spends that.
    if preamble := _platform_preamble(notices):
        prompt_text = f"{preamble}\n\n{prompt_text}"
    # 额度快用完了（`usage.alerts`）：只有这一轮会读到，不落到频道里。它不是
    # 项目设置的解释，是平台自己核得上的事实和它的后果。
    if credits_note:
        prompt_text = f"{platform_prompt(credits_note)}\n\n{prompt_text}"
    # 重放可见 (#416): count this attempt on the blocks themselves. A
    # batch the session never read is not stamped `consumed_turn`, so the
    # SAME batch is re-sent with the next prompt, and the next — correct (a
    # prompt that never arrived must not eat a message) but silent. From
    # the room, "every reply fails" and "this one batch keeps failing" look
    # identical, and the second one is the diagnosis. A batch that was read
    # is consumed then, and is not re-sent whatever the work did next; one
    # read into work that failed says so itself (`READ_FAILED_META_KEY`).
    # Counting at prompt-build time is the only place that sees an attempt
    # that never arrived at all.
    if recipient_instance_id is not None and place.task is None:
        await require_pinned_seat(session, place.room_id, recipient_instance_id)
    replay_n = await count_prompt_attempt(session, prompt_pending_ids, turn_id)
    # Committed here: nothing later in this session commits, and a
    # counter rolled back silently is the exact failure mode it exists
    # to expose.
    await session.commit()
    replay_notice = _replay_notice(replay_n, pending)
    # turn 活跃度检测: a driven runtime judges liveness itself
    # (docs/agent-liveness.md) and holds its own inner ceiling
    # (which can be hours), so the outer wall-clock wrap (runtime.py) must
    # be told the REAL ceiling via a `turn_ceiling` frame instead of
    # killing the turn at the generic `agent_turn_timeout_s`. Without this
    # the device's own two-layer fix is dead on arrival — the outer guard
    # still kills at 900s.
    if recipient_instance_id is not None and place.task is None:
        await require_pinned_seat(session, place.room_id, recipient_instance_id)
    return prompt_text, replay_notice
