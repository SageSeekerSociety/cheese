"""Platform instructions shared by every agent harness."""

import re
import uuid

from app.domain.block.models import BlockKind

_BARE_PATH_RE = re.compile(
    r"(?<![\w/.&<-])((?:[\w.-]+/)+[\w-]+\.\w{1,8}(?::\d+(?:-\d+)?)?)(?![\w/])"
)


def chipify_paths(fact: str) -> str:
    return _BARE_PATH_RE.sub(r"<&\1>", fact)


def build_system_prompt(
    base: str,
    skills: str,
    doc: str | None,
    memories: list[str],
    role: str | None = None,
    roster: list[dict] | None = None,
    topics: list[dict] | None = None,
    untitled: bool = False,
    turn_meta: list[str] | None = None,
    stage_guide: str | None = None,
    memories_omitted: int = 0,
    memories_core_omitted: int = 0,
) -> str:
    parts = [base]
    if untitled:
        # First in the prompt on purpose: naming the topic is the FIRST action
        # of the session — before the opening reply, before any other tool —
        # so the rail never shows a working-but-unnamed 「新话题」.
        parts.append(
            "## 本轮第一件事：先给本话题起名（先于一切）\n"
            "本话题还叫「新话题」（未命名）。**本轮的第一个动作**——在说开场白、"
            "回复任何内容、调用任何其他工具之前——先根据用户的需求执行 "
            '`cheese_title(text="<标题>")` 起个 ≤12 字简短标题，'
            "然后再照常回应、干活。"
            "这条优先于「先回应，再干活」：起标题只是一次工具调用，几乎不花时间。"
            "（只起一次，定了别反复改。）"
        )
    if role:
        parts.append(f"## 你的专家角色\n{role}")
    if skills:
        parts.append(skills)
    if stage_guide:
        # 按阶段渐进式披露: the flow knowledge for THIS point in the topic's
        # lifecycle only. Statically injected (like every other skill) — the
        # model never gets to decide whether to load it, which is the whole
        # reason this isn't a lazily-read Agent Skill (see stages.py).
        parts.append(
            "## 当前阶段的操作说明（平台按本话题所处的流程阶段自动选出，"
            "只给你这一段）\n" + stage_guide
        )
    if topics:
        lines = "\n".join(f"- {t['title']}" for t in topics)
        parts.append(
            "## 项目话题（交叉引用某个话题/它的文档时，在标题前加 @，如 "
            "`@搭建推荐算法原型`——会渲染成可点的「#标题」链接）\n"
            "下面**只列当前活跃的话题**。项目里还有已归档的话题，它们照常存在、"
            "内容也照常可读，只是不在这里列出来；**没列出来 ≠ 不存在**。需要找"
            "它们时自己查（返回全部话题，含 archived 的标题和 id）：\n"
            '`platform_request(method="GET", '
            'path="/topics?project_id=<本项目 id>")`\n'
            "拿到 id 后用 `<#id>` 就能精确引用任何一个话题（包括没列在下面的）。\n"
            + lines
        )
    if roster:
        lines = "\n".join(
            f"- {m['name']}（{m['role']}，handle: {m['handle']}）" for m in roster
        )
        parts.append(
            "## 项目成员 & 怎么点名\n"
            "要让某人去做事/通知到他，**在他名字前加 @**（如 `@张衡`，名字用下表"
            "准确值）——平台会把它变成可点的「@张衡」链接并给他**强提醒**。"
            "只写名字而不加 @ 只是普通文字，不会通知。\n" + lines
        )
    if doc:
        parts.append(
            "## 当前话题的实况文档（这是最新状态；用户可能编辑了它，"
            "请按它继续工作，并在状态变化时用 update_doc 工具更新它）\n" + doc
        )
    if memories or memories_omitted:
        block = "## 项目记忆（你已知道的事实，回答时可引用）"
        if memories:
            block += "\n\n### 核心记忆（每轮都在场，与本轮说什么无关）\n" + "\n".join(
                f"- {chipify_paths(m)}" for m in memories
            )
        if memories_omitted:
            # 没注入必须可见: the pool's size is stated even though its contents
            # are not. A reader who cannot tell "nothing was stored" from "this
            # is not everything" stops trusting memory entirely — and stops
            # asking for the part it can still get. This line is the only entry
            # to that part, so it says the number and how to reach it.
            block += (
                f"\n\n> 📚 记忆池里另有 **{memories_omitted} 条**，"
                "**不会自动出现在这里**——核心记忆之外的都要自己查。"
                "开工前、话题拐弯时、要用到某条旧约定或踩过的坑时，"
                '用 `cheese_recall(query="<关键词>")` 查一次。'
                "**一次没查到不等于没有**：换个说法、或只用其中一两个关键词再试一次。"
            )
        if memories_core_omitted:
            # Core is the layer that is supposed to be unconditional. If even
            # it had to be cut, saying so is the only way it gets pruned.
            block += (
                f"\n\n> ⚠️ **核心记忆超预算了**：有 {memories_core_omitted} 条核心记忆"
                "没放下。核心记忆本该每轮全在场，出现这种情况说明它被当成普通记忆写"
                "了——挑几条降级成普通记忆（`cheese_remember` 不带 `core`）。"
            )
        parts.append(block)
    if turn_meta:
        parts.append(
            "## 本轮运行环境（平台元信息，非用户输入）\n" + "\n".join(turn_meta)
        )
    return "\n\n".join(parts)


KICKOFF_PROMPT = (
    "这个话题刚从一条消息升级出来，由你负责推进。任务简报在系统提示的"
    "「当前话题的实况文档」里：被升级的那段讨论 + 它原来所在地方的文档快照。"
    "现在开工：\n"
    "1. 先发开场白：一两句复述你理解的任务、说明打算怎么推进（给人纠偏的机会）；"
    "简报信息不足就明确列出缺什么、@ 升级发起人补充。\n"
    "2. 把实况文档改写成你自己的状态摘要（目标/约束/下一步），别留着简报原文不动。\n"
    "3. 能直接开始的活就开始干；需要拍板的用决策请求找对的人。"
)


def thread_relay_prompt(
    *, task_id: uuid.UUID, task_title: str, author: str, message: str
) -> str:
    """The ROOM's wake-up instruction when a person says something on one of its
    threads — a chat message, a comment on its living doc.

    Same reason as 补证据 and 讨论升级: the person is looking at the thread, but
    the worker doing it lives in the room's session, so the room is the only
    thing that can hear them. What was said stays where it was said — this only
    says who has to act on it.
    """
    return (
        f"有人在活「{task_title}」（task id `{task_id}`）上说话了：\n\n"
        f"---\n[{author}] {message}\n---\n\n"
        "**转达给做这条活的分身**：它还在跑就直接给它发消息；已经收工了，你就自己"
        "看着办——能替它答的当场答，要接着干的照原来的简报重起一个分身并 "
        f'`cheese_bind(task_id="{task_id}", agent_id=<新的 agent_id>)`。'
        "回话说在这条活上（`cheese_tell` 到它），别只在房间里说，"
        "问话的人看的是那边。"
    )


def thread_upgraded_prompt(*, task_id: uuid.UUID, source_message: str) -> str:
    """The ROOM's wake-up instruction when one of its messages became a thread.

    Addressed to the room because a thread is a 分身 inside the room's own
    session and has no session to wake. The platform writes the row, its card
    block and its brief; raising the worker is the room's, and so is naming the
    thread — it is created untitled and nothing else is in a position to name it.
    """
    return (
        f"你把一条消息升级成了这个房间里的一条活（task id `{task_id}`）。"
        "被升级的那段话就是它的简报，平台已经记在卡上了：\n\n"
        f"---\n{source_message}\n---\n\n"
        "接下来是你的事：\n"
        f'1. `cheese_title(text="<≤12 字的标题>", task="{task_id}")`'
        "——它现在还叫「新话题」，"
        "只有你能给它起名字。\n"
        "2. 用你的 Agent 工具起一个分身，**把上面这段简报原文放进它的 prompt**"
        "（分身不会自己去读文档）。\n"
        f'3. `cheese_bind(task_id="{task_id}", agent_id=<分身的 agent_id>)`'
        "——不 bind，这条活在界面上"
        "永远是「没人做」，分身干的每件事都记在你头上。"
    )


PLATFORM_NOTICE = "【平台】以下是平台自动发出的指令，不是任何人手打的话："


def platform_prompt(content: str) -> str:
    return f"{PLATFORM_NOTICE}\n{content}"


def publication_prompt(content: str, *, is_private: bool = False) -> str:
    """Carry the chat contract on new and resumed terminal input alike."""
    if is_private:
        return (
            content
            + "\n\n"
            + platform_prompt(
                "这是私聊，最终答复会自动发布给用户。直接回答，"
                "不要再用 chat_send 重复发送同一答复。"
            )
        )
    return (
        content
        + "\n\n"
        + platform_prompt(
            "Ordinary output and final responses are not published to chat. "
            "Publish with the chat_send tool. "
            "收到需要回应的用户消息（包括排队或执行中追加的消息）时，能直接回答就发答案；"
            "需要继续处理就先说明你理解的意思和接下来要做什么，再继续。"
            "重要进展、改方向、阻碍和完成结果也要主动发消息。"
            "巡检按 heartbeat 的通知规则发言；分身向主 agent 回报。"
        )
    )


def strip_platform_notice(text: str) -> str:
    """Neutralize the platform marker inside HUMAN text, so a person cannot type
    a message that reads as a platform instruction. The marker is the one thing
    in the prompt that claims institutional authority, so it has to be
    unforgeable from the content side."""
    return text.replace(PLATFORM_NOTICE, "【平台·用户原文】")


def attachment_prompt_line(
    author: str, path: str, *, embeds_images: bool, mime: str = "image/png"
) -> str:
    if not mime.startswith("image/"):
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


def prompt_line(b, *, embeds_images: bool) -> str:
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
    not read the path."""
    if b.kind == BlockKind.attachment:
        return attachment_prompt_line(
            b.author, b.content, embeds_images=embeds_images, mime=b.mime_type or ""
        )
    return f"[{b.author}]: {strip_platform_notice(b.content)}"
