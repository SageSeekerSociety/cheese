"""进 prompt 的那部分上下文：开场事实、进度层、话题引用表、待读输入与平台通知。

一轮的提示词里，有一半不是「问什么」，而是「问之前先摆出来的事实」——这台机器
多大、上次做到哪了、这间房有哪些话题、哪些消息还没有任何一轮读过、平台改了什么
还没说。它们都是纯函数：入参是已经读好的行、块和模型对象，出参是拼好的文本行，
不碰会话、不碰轮次状态、不落库。

放在一起，是因为它们答的是同一个问题：**一个新开的会话（或者一轮被重启的会话）
自己查不到的东西是什么**。散着放时，判据会各自漂移——同一件事有两份说法，两份
会各说各话（`_topic_ref_lists` 与 `_prompt_topic_refs` 成对返回、必须保持不同，就是
这条的一个实例）。

曾经它们散在 `chat.py` 里，夹在 `_assemble_turn`（读数据的那半边）与落库之间。
调用它们的是房间那一侧：`room/turn.py` 的 `_assemble_turn`，chat.py 的
`has_unread_input`、`_pending_receipts` 一族、`_restate_note`，以及
`post_system_event` 的两个 compaction 分支。搬出来时按原样搬——入参出参就是
它们与调用方之间全部的约定，所以行为一格没动。
"""

import uuid
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import say
from app.domain.agent.harness.prompt import (
    is_inline_image,
    platform_prompt,
    strip_platform_notice,
)
from app.domain.agent.platform_notices import (
    EVENT_CONTEXT_COMPACT,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_PLATFORM,
    notice,
)
from app.domain.agent.service import AgentCompacting
from app.domain.agent.session_host.contract import Image
from app.domain.block.authorship import is_participant
from app.domain.block.models import (
    CONSUMED_TURN_META_KEY,
    Block,
    BlockKind,
    agent_notice,
    consumed_turn,
)
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.library import records as library_records
from app.domain.topic.models import (
    Topic,
    TopicKind,
    TopicStatus,
)

_PROGRESS_MARK = {"completed": "x", "in_progress": "~", "pending": " "}


def _progress_lines(items: list[dict]) -> list[str]:
    """进度层 (#187): the checklist this topic's work left behind, as prompt text.

    This is the one thing a fresh machine cannot reconstruct from the repo. Code
    survives in git, conclusions survive in the doc and the conversation, but
    "which of the five things am I on" only ever lived in the dead turn's stream.
    So it is stated here as a fact about the topic, not as memory — see
    TopicProgress's docstring for why the two must not be merged.

    The instruction to re-list finished items when building a new checklist is
    load-bearing: `todo_write` replaces the stored row whole, so a plan that
    silently drops what is already done would erase it.
    """
    if not items:
        return []
    lines = ["- 上次的任务清单（跨轮、跨机器保留下来的进度，不是这一轮新建的）："]
    for item in items:
        mark = _PROGRESS_MARK.get(str(item.get("status", "")), " ")
        subject = str(item.get("subject", "")).strip() or "（任务）"
        lines.append(f"    - [{mark}] {subject}")
    lines.append(
        "  已完成的别重做，接着没做完的往下干。**用 `todo_write` 重新写清单时把已完成"
        "的也列进去并标成 completed**——清单会整份覆盖上面这份，只列剩下的等于把做过"
        "的抹掉。"
    )
    return lines


def _sandbox_limits(provider: object) -> tuple[int, int] | None:
    """(memory_mb, cores) for backends that know their own size, else None.

    Read off the provider rather than looked up in the machine tables on
    purpose: #282 决定 2 keeps the agent layer out of the machine domain, and
    check-repo-rules enforces it. A backend that does not set these attributes
    genuinely does not know — an enrolled machine belongs to someone else and we
    do not set its limits — and None then means the prompt says nothing at all.
    Inventing a number would be worse than silence: the agent would skip work it
    could have done.
    """
    memory_mb = getattr(provider, "sandbox_memory_mb", None)
    cores = getattr(provider, "sandbox_cores", None)
    if isinstance(memory_mb, int) and isinstance(cores, int) and memory_mb > 0:
        return (memory_mb, cores)
    return None


def _session_opening_lines(
    *,
    progress: list[dict] | None = None,
    sandbox: tuple[int, int] | None = None,
    earlier_messages: int = 0,
    unconnected_mcp: tuple[str, ...] = (),
) -> list[str]:
    """盲飞防护: what a session cannot find out for itself, at the moment it opens.

    Each survives being written once. The machine's size does not change under
    a session; the checklist answers 「我做到哪了」 and the pointer to the chat
    answers 「之前说了什么」 for a session that was not there — once one is
    running, its own history answers both.

    This is what is left of a per-turn header that came from #175, where the
    complaint was 29 turns timing out against a 900s ceiling nobody had been
    told about. Two things happened to it. The ceiling went away (there is no
    countdown; saying there was one made the agent rush — dev, 2026-08-08), and
    `cheese_status` — added in that same commit, for that same complaint — took
    over the rest: cards, gate output and disk are all one call away, and the
    header was restating them every turn, from a snapshot that stopped being
    true after the first one. What is left is the part no call and no turn can
    reconstruct.
    """
    lines: list[str] = []
    # 机器有多大: the agent cannot read its own cgroup limit, and the failure it
    # produces without knowing — a build the kernel OOM-kills — looks like a
    # broken toolchain rather than a small box. Only the FACT goes here; what to
    # do about it (try it once anyway, never retune --max-old-space-size, say
    # plainly that you did not run it) is a principle and lives in the cheese
    # skill. None means this backend does not know its own size, and then we say
    # nothing at all rather than invent a number.
    if sandbox is not None:
        mem_mb, cores = sandbox
        gb = mem_mb / 1024
        shown = f"{gb:.0f}" if gb == int(gb) else f"{gb:.1f}"
        lines.append(
            f"- 这台机器：内存 {shown}GB、{cores} 核。吃内存的命令"
            "（前端 build/typecheck、大型编译）可能被内核 OOM 杀掉——那不是代码"
            "有问题，也不是工具链坏了。"
        )
    lines.extend(_progress_lines(progress or []))
    # A server the project's .mcp.json names but nobody has connected yet: the
    # agent would otherwise go looking for tools that are not there, or take
    # their absence for a broken setup. What to do about it is a person's.
    if unconnected_mcp:
        lines.append(
            "- 项目的远程 MCP 服务器 "
            + "、".join(unconnected_mcp)
            + " 需要项目成员在项目设置里连接，这个会话里用不了它们的工具。"
        )
    # A session that opens in a room with history has read none of it, while
    # the people in the room assume it has. The living docs, memory and the
    # checklist reach it as conclusions; what was said is only in the chat.
    if earlier_messages:
        lines.append(
            f"- 这里已经有 {earlier_messages} 条聊天消息，这个会话一条都没读过。"
            "动手之前先用 `cheese_chat_list` 读最近的记录；"
            "要找某句原话或某个决定，用 `cheese_chat_search`。"
        )
    return lines


def _platform_preamble(notices: list[Block]) -> str:
    """What moved under the session, as the frame the rest of the prompt is read
    in — so it goes first: a request to revise the 验收标准 means something else
    once you know that section moved ten minutes ago.

    One marker over all of them. The marker is the one thing in a prompt that
    claims institutional authority, and repeating it per line spends that.

    Neutralized exactly as the live push neutralizes it: a notice quotes what
    people typed — a document's own headings — so the marker must not be
    forgeable from the content side.
    """
    said = "\n".join(str(agent_notice(b)) for b in notices)
    return platform_prompt(strip_platform_notice(said)) if said else ""


def _resume_notice() -> str:
    """The one thing that is true of a turn rather than of its session."""
    return (
        "本轮接着上一轮跑：上一轮中途断了，这是同一件事的继续。"
        "先确认上一轮做到哪了再继续（翻消息记录、git status），别凭印象重做。"
    )


def _topic_ref_lists(
    topics: list[Topic], *, exclude_id: uuid.UUID
) -> tuple[list[dict], list[dict]]:
    """一次推导出两份话题列表：`(全量解析表, 渲染进 prompt 的子集)`。

    故意成对返回：这两份**必须**从同一批话题推导，且**必须**保持不同。全量那份
    喂给 `expand_mention_names`（`@标题` → `<#id>` 的解析表，含已归档话题）；子集
    那份只喂给 `build_system_prompt`。合成一份就会把"少注入"变成"少了引用能力"
    ——用户自己打 `@某个已归档话题` 会不再变成链接。
    """
    visible = [t for t in topics if t.id != exclude_id and t.kind != TopicKind.root]
    return [{"id": str(t.id), "title": t.title} for t in visible], _prompt_topic_refs(
        visible
    )


def _prompt_topic_refs(topics: list[Topic]) -> list[dict]:
    """渐进式披露：从全量话题里挑出**值得渲染进 system prompt** 的那一小撮。

    只影响 prompt 里列出来的那一段；`expand_mention_names` 拿到的仍是全量列表，
    所以过滤掉的话题（含已归档的）用 `@标题` 照样解析得出 <#id> 链接——少注入是
    纯赚的，不损失任何引用能力。

    剔除两类：
    - 已归档：本项目实测占注入量的 74%，而引用一个几周前归档的话题几乎没有价值；
      需要时 agent 自己查（prompt 那段里给了查法）。
    - 同名：`expand_mention_names` 对同名标题只解析第一个（mentions.py 的 `seen`
      去重），其余会**静默指向错的那一个**。所以同名的**全部剔除**而不是留一个
      ——留一个等于在 prompt 里推荐一个会指错的引用；全部不列，它们仍可通过查询
      拿到 id 后用 <#id> 精确引用。
    """
    live = [t for t in topics if t.status != TopicStatus.archived]
    titles = Counter(t.title for t in live)
    return [{"id": str(t.id), "title": t.title} for t in live if titles[t.title] == 1]


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


async def live_inputs(
    session: AsyncSession, block_ids: list[uuid.UUID]
) -> tuple[Block | None, Block | None]:
    """Read the persisted authored message, quote and validated reply edge."""
    stored = replied = None
    for block_id in block_ids:
        block = await session.get(Block, block_id)
        if block is not None:
            if block.kind == BlockKind.message:
                stored = block
            if block.reply_to is not None:
                replied = await session.get(Block, block.reply_to)
    return stored, replied


async def read_images(
    session: AsyncSession, room: Topic | None, images: list[dict]
) -> list[Image]:
    """The pictures said along with words mid-turn, read now: the session that
    is handed them reads no files of its own. No room, no pictures."""
    if room is None:
        return []
    return [
        Image(
            image["media_type"],
            await library_records.read_attachment(
                session, room.project_id, room.id, image["path"]
            ),
        )
        for image in images
    ]


async def offered_attachments(
    session: AsyncSession,
    pending: list[Block],
    project_id: uuid.UUID,
    room_id: uuid.UUID,
) -> tuple[list[Image], set[uuid.UUID]]:
    """The images a turn hands the session, read now, and the attachments whose
    file is gone.

    A message outlives its file: a library file can be deleted while a message
    still references it. Offering that file fails the read, the turn ends
    unfinished, the message stays unconsumed, and every later turn replays it
    and fails the same way. So a missing file is not offered; its prompt line
    says it is gone (`prompt_line(gone=True)`) and the turn goes on.
    """
    attachments = [b for b in pending if b.kind == BlockKind.attachment and b.content]
    gone = {
        b.id
        for b in attachments
        if not await library_records.attachment_exists(
            session, project_id, room_id, b.content
        )
    }
    images = [
        Image(
            b.mime_type or "",
            await library_records.read_attachment(
                session, project_id, room_id, b.content
            ),
        )
        for b in attachments
        if b.id not in gone and is_inline_image(b.mime_type)
    ]
    return images, gone


def _addressed_to(block: Block, handle: str) -> bool:
    """Is this input for the agent `handle`? One without a recipient is for
    whichever agent the room resolves to, which the caller passes in."""
    return (block.meta or {}).get("agent_recipient", {}).get("handle", handle) == handle


def _pending_platform_notices(history: list[Block]) -> list[Block]:
    """What the platform has to say to 芝士 and has not managed to say yet.

    A turn is built on a snapshot taken when the SESSION started — the document,
    the roster, the cards — and the session outlives many turns. Everything that
    can invalidate that snapshot is something the platform did, so the code that
    did it leaves a sentence on the block it was already writing, and this reads
    whatever nobody has read yet.

    No watermark and no legacy fallback, unlike the human window above: a notice
    is pending exactly while it has something to say and no turn has stamped it,
    and blocks written before this existed say nothing to 芝士 at all.
    """
    return [b for b in history if agent_notice(b) and consumed_turn(b) is None]


# 重放可见 (#416). The first notice fires on the third attempt: one retry is
# ordinary (a transient provider error, an auto-resume), two is bad luck, three
# is a pattern worth a line in the room. After that the state is known, so the
# reminder throttles hard — a topic retrying every 5 minutes for an hour must
# not bury the conversation under its own status.
_REPLAY_NOTICE_AT = 3
_REPLAY_NOTICE_EVERY = 10


def _replay_notice(attempt: int, pending: list[Block]) -> str | None:
    """The 现场 line for a batch of messages that keeps being re-sent.

    Returns None when there is nothing worth saying yet — the common case.

    ONE line, and it stays one line at any batch size. It names the count and
    the OLDEST message — the one stuck longest, and the one that identifies the
    batch. "这个话题重试了 5 次" leaves the reader exactly where they started;
    dumping all N messages back into the room turns a status line into a second
    copy of the conversation. The messages are already in the timeline right
    above; the notice only has to point at them.
    """
    if attempt < _REPLAY_NOTICE_AT:
        return None
    if attempt > _REPLAY_NOTICE_AT and attempt % _REPLAY_NOTICE_EVERY != 0:
        return None
    first = pending[0] if pending else None
    head: str
    if first is None:
        head = ""
    elif first.kind == BlockKind.attachment:
        head = say("promptReplayedOldestImage", author=first.author)
    else:
        text = " ".join((first.content or "").split())
        clipped = f"{text[:24]}…" if len(text) > 24 else text
        head = say("promptReplayedOldestText", author=first.author, text=clipped)
    return say("promptReplayed", count=len(pending), attempt=attempt, oldest=head)


def _compaction_notice(event: AgentCompacting) -> tuple[str, dict]:
    """The room's line for a compaction: while it runs, and once it is over.

    One line per compaction, restated in place: it says why the session is
    silent while it runs, and whether it came back once it has ended."""
    if not event.done:
        content = say("contextCompactRunning")
        severity, detail = SEVERITY_INFO, None
    elif event.error:
        content = say("contextCompactFailed")
        severity, detail = SEVERITY_WARN, event.error
    else:
        content = say("contextCompactDone")
        severity, detail = SEVERITY_INFO, None
    meta = {
        **notice(
            EVENT_CONTEXT_COMPACT,
            severity=severity,
            who=WHO_PLATFORM,
            detail=detail,
            detail_label=say("labelReason") if detail else None,
        ),
        "state": "over" if event.done else "running",
        "at": datetime.now(UTC).isoformat(),
    }
    return content, meta
