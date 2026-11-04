"""一条消息在提示词里长什么样：说话人行、附件行、回复引用。

从一条 block 变成一行文本的那一段。只依赖 block 模型和平台标记，不碰数据库、
不碰会话状态：入参是已经读好的块和它的附件信息，出参是拼好的一行或几行。开局那批
待读消息和会话中途推进来的消息都走这里，所以「一条消息读起来是什么样」只有一份。

**这里的措辞是承重的，不是体面的。** 附件行必须说清图片这一轮到底附上了没有——
不同骨架不一样——说错了 agent 不会报错，它会就一张没看过的图写出一个自信的答案，
下游没有任何地方会把那个答案标成编的。`prompt_line` 的说明里记着这一次。
"""

import re
from collections.abc import Collection
from datetime import UTC, datetime

from app.core import raster_images
from app.core.config import settings
from app.domain.agent.harness.notices import strip_platform_notice
from app.domain.block.authorship import speaker
from app.domain.block.models import BlockKind
from app.domain.block.quoted_context import quoted_context_prompt
from app.domain.identity.handles import recipient_seat

_BARE_PATH_RE = re.compile(
    r"(?<![\w/.&<-])((?:[\w.-]+/)+[\w-]+\.\w{1,8}(?::\d+(?:-\d+)?)?)(?![\w/])"
)


def chipify_paths(fact: str) -> str:
    return _BARE_PATH_RE.sub(r"<&\1>", fact)


def is_inline_image(mime: str | None) -> bool:
    """Only native raster formats belong in the model's image input.

    Unknown types and SVG stay files, readable on demand rather than encoded
    into every turn. Keep initial, live and textual routing on this rule.
    """
    return raster_images.is_raster(mime)


def attachment_prompt_line(
    author: str,
    path: str,
    *,
    embeds_images: bool,
    mime: str = "",
    gone: bool = False,
    undecodable: bool = False,
) -> str:
    if gone:
        # The file was deleted after the message (see `offered_attachments`).
        return (
            f"[{author}] 发过一个文件 {path}，但这份文件已经不在了"
            f"（多半是从资料库里删掉了），这次读不到它的内容。"
            f"需要它的话，请对方重新发一份。"
        )
    if undecodable:
        # Named or sent as a picture, but its bytes do not decode or are more
        # than a model takes in one picture (`model_images`). Read would hand it
        # to the model as a picture all the same, and the model's API refuses
        # every later request of the conversation over it.
        return (
            f"[{author}] 发来一个文件：{path}。它看起来是图片，但内容解不开或者文件"
            f"太大，不能当图片看。不要用 Read 打开它；要查看，用命令行工具检查它的字节。"
        )
    if not is_inline_image(mime):
        return f"[{author}] 发来一个文件：{path}。请用适合该格式的工具读取文件内容。"
    if embeds_images:
        return (
            f"[{author}] 发来一张图片（图片内容已附在本条消息里；"
            f"它同时存在你工作目录的 {path}）"
        )
    return (
        f"[{author}] 发来一张图片：**它没有附在本条消息里**，"
        f"文件在你工作目录的 {path}，需要你自己用 Read 打开它。"
        f"（打不开就直说打不开，不要猜图里是什么。）"
    )


# A quote is there to say WHICH message a reply answers and what it said, not to
# replay it: a long report 芝士 wrote is already in its own session, and a reply
# to it usually names one point. The cap keeps one reply from carrying pages.
REPLY_QUOTE_LIMIT = 1500


def reply_quote(parent, *, recipient: str | None) -> str:
    """The lines under a reply that quote the message it answers.

    In the room the reply shows its parent; the prompt used to carry only the
    reply's own words, so 「@芝士 改一下这条」 reached 芝士 without 「这条」. The
    parent is often not in the backlog either — an earlier turn already read it.

    Every line starts with ``>`` so none of it can pass for a speaker line, and
    it is quoted text in any case: the parent's author said it, not the person
    replying."""
    if parent.kind == BlockKind.attachment:
        body = f"（文件 {parent.content}）"
    else:
        body = strip_platform_notice(parent.content).strip()
        if len(body) > REPLY_QUOTE_LIMIT:
            body = body[:REPLY_QUOTE_LIMIT] + "…"
    whose = "你之前" if parent.author == recipient else f" [{parent.author}] "
    lines = body.splitlines() or [""]
    quoted = "\n".join(f"> {line}" if line else ">" for line in lines)
    return f"> 回复的是{whose}的这条消息：\n{quoted}"


def message_prompt_line(
    author: str,
    content: str,
    quoted_context: dict | None = None,
    *,
    addressed_to: str | None = None,
) -> str:
    """One line, speaker-labelled; ``→ @handle`` when it names somebody else.

    A seat now reads what was said to the whole room, so most of what it reads
    was not said to it. The arrow is what keeps 「read everything」 from turning
    into 「answer everything」: the line itself says whose it was.
    """
    who = f"{author} → @{addressed_to}" if addressed_to else author
    return f"[{who}]: {strip_platform_notice(content)}" + quoted_context_prompt(
        quoted_context
    )


def addressee(b, *, recipient: str | None, recipient_instance_id=None) -> str | None:
    """The seat a block names, when that is not the reader; else None.

    ``recipient``/``recipient_instance_id`` are who is reading. A caller that
    knows neither — a line injected into a running turn, which is by definition
    for the seat it is injected into — gets no arrow: guessing from the block
    alone would label the reader's own message as somebody else's.
    """
    if recipient is None and recipient_instance_id is None:
        return None
    recipient_meta = (b.meta or {}).get("agent_recipient")
    if not recipient_meta:
        return None
    named_instance = recipient_meta.get("instance_id")
    if named_instance is not None and recipient_instance_id is not None:
        if str(named_instance) == str(recipient_instance_id):
            return None
    elif recipient_meta.get("handle") == recipient:
        return None
    return recipient_seat(recipient_meta)


def waited_note(sent_at: datetime | None, now: datetime | None = None) -> str | None:
    """The line under a message that reaches its reader long after it was sent:
    when it was sent (UTC, as every time a tool shows) and how long ago.

    A message can sit for days before a turn carries it — behind a turn that
    never ended, or an input nothing would settle. Its line alone reads as if
    just said, and the reader answers a stale question as a current one. Past
    as long as a turn may go without a word (``turn_silence_timeout_s``) it is
    no longer that.
    """
    if sent_at is None:
        return None
    waited_s = ((now or datetime.now(UTC)) - sent_at).total_seconds()
    if waited_s <= settings.turn_silence_timeout_s:
        return None
    if waited_s >= 86400:
        span = f"{int(waited_s // 86400)} 天"
    elif waited_s >= 3600:
        span = f"{int(waited_s // 3600)} 小时"
    else:
        span = f"{int(waited_s // 60)} 分钟"
    return (
        f"（这条发于 {sent_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC，到现在已过 {span}；"
        "回答前先确认现在的情况）"
    )


def prompt_line(
    b,
    *,
    embeds_images: bool,
    replied=None,
    recipient: str | None = None,
    recipient_instance_id=None,
    gone: bool = False,
    undecodable: bool = False,
) -> str:
    """One speaker-labelled prompt line per pending human block.

    An attachment block is a worktree image, and the line has to describe how it
    actually arrives THIS turn — which is not the same on every backend:

    - ``embeds_images``: the provider produces a native image block. SDK/relay
      providers embed base64 directly; interactive Claude Code resolves the
      prompt's ``@path`` through its native attachment path after a remote
      device has acknowledged staging the bytes.
    - a third-party provider that declares ``embeds_images=False`` gets the
      explicit Read fallback and must not claim the image was attached.

    The wording is load-bearing, not cosmetic. Told "图片内容已附在本条消息里"
    and handed nothing, an agent does not raise — it writes a confident answer
    about a picture it never saw, and nothing downstream marks that answer as
    invented. Saying "去打开这个文件" fails safe: worst case it reports it could
    not read the path.

    ``replied`` is the block this one answers, when it is a reply: its quote
    follows the line (`reply_quote`). ``gone`` says the attachment's file no
    longer exists; ``undecodable`` that it looks like a picture and is not one."""
    if b.kind == BlockKind.attachment:
        line = attachment_prompt_line(
            speaker(b),
            b.content,
            embeds_images=embeds_images,
            mime=b.mime_type or "",
            gone=gone,
            undecodable=undecodable,
        )
    else:
        line = message_prompt_line(
            speaker(b),
            b.content,
            (b.meta or {}).get("quoted_context"),
            addressed_to=addressee(
                b, recipient=recipient, recipient_instance_id=recipient_instance_id
            ),
        )
    waited = waited_note(getattr(b, "created_at", None))
    if waited is not None:
        line = f"{line}\n{waited}"
    if replied is None:
        return line
    return f"{line}\n{reply_quote(replied, recipient=recipient)}"


def live_input_lines(
    author,
    content,
    attachments,
    *,
    stored=None,
    replied=None,
    recipient=None,
    undecodable: Collection[str] = frozenset(),
):
    """Live input reads the same saved quote and formatting as initial input.

    ``undecodable`` holds the paths among ``attachments`` that look like
    pictures and do not decode (`model_images`)."""
    lines = []
    if content:
        lines.append(
            prompt_line(stored, embeds_images=True)
            if stored is not None
            else message_prompt_line(author, content)
        )
    files = [
        {"path": str(a["path"]), "media_type": str(a.get("mime") or "")}
        for a in attachments or []
        if a.get("path")
    ]
    lines.extend(
        attachment_prompt_line(
            speaker(stored) if stored is not None else author,
            f["path"],
            embeds_images=True,
            mime=f["media_type"],
            undecodable=f["path"] in undecodable,
        )
        for f in files
    )
    if replied is not None:
        lines.append(reply_quote(replied, recipient=recipient))
    return lines
