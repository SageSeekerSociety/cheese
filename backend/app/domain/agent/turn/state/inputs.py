"""Pure eligibility and recipient facts shared by preparation and completion."""

from app.domain.block.authorship import is_participant
from app.domain.block.models import (
    CONSUMED_TURN_META_KEY,
    Block,
    BlockKind,
    consumed_turn,
)
from app.domain.identity.handles import looks_like_agent_handle


def _is_pending_input(b: Block) -> bool:
    """A block carrying something a participant said into the room.

    「参与者」而不是「人」：一个 AI 队友在房间里说的一句话，对坐在同一个房间里
    的另一个参与者同样是这一轮要读的输入（结论 1）。挡住「芝士自己这一轮的产
    出」的不是这里，而是写入端 —— agent 署名**且**落在某一轮里的块根本不盖
    pending 标记（`BlockRepository.add`）。
    """
    return is_participant(b.author_type) and b.kind in (
        BlockKind.message,
        BlockKind.attachment,
    )


def _pending_input_blocks(history: list[Block]) -> list[Block]:
    """The messages/attachments no turn has read into a prompt yet.

    轮次边界按**归属**划，不按位置划：一条在轮次运行中到达的人类消息，created_at
    排在那轮 AI 回复之前，所以"最后一条 AI 消息之后"这个窗口会把它切掉 —— 而且
    切掉就再也捡不回来了（那个下标只会往前走）。这里改成挑「没被任何一轮盖过
    consumed 戳」的块，戳由干净收尾的轮次盖上（BlockRepository.mark_consumed）。

    New inputs carry an explicit ``consumed_turn: null`` marker while pending.
    That presence matters: a newer mid-turn input can be receipted before an
    older queued attachment, so no consumed block may act as a positional
    watermark over another tracked input. Legacy blocks have no marker and keep
    the old "after the latest AI message" fallback.

    `history` 已按 created_at 升序。
    """
    legacy_watermark = -1
    for i, b in enumerate(history):
        if looks_like_agent_handle(b.author) and b.kind == BlockKind.message:
            legacy_watermark = i
    return [
        b
        for i, b in enumerate(history)
        if _is_pending_input(b)
        and consumed_turn(b) is None
        and (CONSUMED_TURN_META_KEY in (b.meta or {}) or i > legacy_watermark)
    ]


def _addressed_to(block: Block, handle: str) -> bool:
    """Is this input for the agent `handle`? One without a recipient is for
    whichever agent the room resolves to, which the caller passes in."""
    return (block.meta or {}).get("agent_recipient", {}).get("handle", handle) == handle
