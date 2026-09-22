"""Platform instructions shared by every agent harness."""

import re
import uuid

from app.domain.block.models import BlockKind

_BARE_PATH_RE = re.compile(
    r"(?<![\w/.&<-])((?:[\w.-]+/)+[\w-]+\.\w{1,8}(?::\d+(?:-\d+)?)?)(?![\w/])"
)


def chipify_paths(fact: str) -> str:
    return _BARE_PATH_RE.sub(r"<&\1>", fact)


#: 结论 52：「prompt 里必须有随时 push，包括主 agent 也是」。它进系统提示词而不是
#: 进 skill，因为它不是默认而是规则：一条活的工作树在做它的那台机器上，子 agent 与
#: 起它的进程同生同死，机器一回收就只剩分支上已经推走的东西，而恢复的办法是从分支
#: 重派一次（结论 43）。只 commit 不 push 的活过不了这台机器。
ALWAYS_PUSH = (
    "## 随时 push（所有 agent，主 agent 也一样）\n"
    "干活期间**随时 push**，不要攒到交付那一下才推。你的工作树在这台机器上，而机器"
    "随时可能被回收；接着干下去的办法是从分支上重来一次，所以没推上去的改动，到不了"
    "下一轮，也到不了任何别人手里。提交了却没推等于没有。"
)


def build_system_prompt(
    base: str,
    skills: str,
    doc: str | None,
    memories: list[str],
    role: str | None = None,
    roster: list[dict] | None = None,
    topics: list[dict] | None = None,
    untitled: bool = False,
    artifacts: list[dict] | None = None,
    overview_doc: str | None = None,
    session_opening: list[str] | None = None,
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
            "`cheese title <标题>` 起个 ≤12 字简短标题，"
            "然后再照常回应、干活。"
            "这条优先于「先回应，再干活」：起标题只是一次工具调用，几乎不花时间。"
            "（只起一次，定了别反复改。）"
        )
    parts.append(ALWAYS_PUSH)
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
    if artifacts is not None:
        # 产物清单进每一轮的开场 (#1085 结论三)。它在这里是为了让下一次交付点得准
        # 名字 —— 而先说清哪一次交付根本不用点名：交出去这次合并本身的，交的是这
        # 个项目的仓库，平台自己认得出是哪一项。那条路上没有名字可写错，也就没有
        # 什么可嘱咐的。
        #
        # 剩下交一份文件、交一个地址的，才真的有得选（一个项目可以既交一份报告又
        # 交一个网站），所以下面那几行是说给它们听的。
        #
        # **清单空着的时候这一段照样出现。** 那是必须说话的那一次：一个交文件的项
        # 目，第一次交付只能新建，而它起的那个名字会留在清单上，进后面每一轮的开
        # 场。这一段不在的话，提示里没有一个字提到产物，只剩递卡被打回这一条路能
        # 让人知道要声明——而递卡是一整轮工作的最后一步。
        head = "## 这个项目的产物清单（交出去的东西，一项一行）\n"
        # 这一版交出去的是什么，也在递卡时说 (#1085 结论五)。它排在最前面，因为它
        # 的答案决定了后面那两段要不要读。
        hands_over = (
            "**先说这一版交出去的是什么**，因为它决定了后面还要不要说别的：\n"
            "- 两个都不给 = 交出去这次**合并**本身（代码仓库这类项目交的就是它）。"
            "这种交付**不用声明产物** —— 交出去的是这个项目的仓库，一个项目只有一"
            "个，平台认得出是清单上哪一项。传了 `artifact` / `new_artifact` 反而会"
            "被打回。\n"
            "- `deliver=<工作目录里的路径>` = 交出去一份文件（平台在递卡这一刻留一"
            "份快照，所以**先把它构建出来再递卡** —— 过了这一轮那份文件就没了）。\n"
            "- `deliver_url=<网址>` = 交出去一个地址。\n"
            "后两种要接着说清动的是清单上哪一项："
        )
        # 那一句话怎么写 —— 规则加检验方法。规则会忘，检验方法当场能自查，所以两
        # 者一起给。同一条检验对名字也成立，因此这里说一次，管名字也管那句话。
        about_rule = (
            "\n\n`about` 那一句话说的是**这样东西本身**（是什么、给谁的），不是这"
            "一版做了什么 —— 这一版做了什么在 `subject` 上，已经有了。三条检验，起"
            "名字用的是同一条第 1 条：\n"
            "1. 这句话（这个名字）在**第 1 版和第 20 版都成立**。一交新版就得改的，"
            "就是写错了。正因为写对了，它不必每版重写。\n"
            "2. 换到清单上另一项头上**也说得通，就是白写**，重写。\n"
            "3. 别把改动标题抄进来 —— 那条路的尽头是清单长成一份改动列表。"
        )
        if artifacts:
            lines = "\n".join(
                f"- 《{a['name']}》"
                + (f"　第 {a['version']} 版" if a["version"] else "　还没交付过")
                + (f"　{a['about']}" if a.get("about") else "")
                + f"　id={a['id']}"
                for a in artifacts
            )
            parts.append(
                head + hands_over + "交付下面某一项的新一版，用 `artifact=<id>` 点名"
                "它（**照抄下面那一行的 id，不要写名字**——名字写错不会报错，只会在清"
                "单上多一项看着像重复的东西）；确实做出了一样下面没有的东西，用 "
                "`new_artifact=<真名>` 加 `about=<一句话>` 声明它，返回里带着新的 id。"
                "两个都不给、或者两个都给，递卡会被打回。" + about_rule + "\n\n" + lines
            )
        else:
            parts.append(
                head
                + "清单还空着，这个项目一样东西都还没交出去过。\n\n"
                + hands_over
                + "清单上还没有可沿用的，所以用 `new_artifact=<真名>` 加 "
                "`about=<一句话>` 声明它，返回里带着它的 id，以后交付它的新一版用 "
                "`artifact=<id>` 点名。" + about_rule
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
    if overview_doc:
        # 人和 agent 共同看的东西是文档，不是一个共享记忆池（结论 7）：每个项目
        # 有一份总览文档，每间房间都读到同一份，谁改了都留痕。所以「所有人都该
        # 知道」的事实写这里，而不是记进记忆——记忆是这一个实例自己的观察。
        parts.append(
            "## 项目总览的实况文档（全项目共看的那一份，不是本话题的）\n"
            "这是这个项目所有人和所有芝士共同看的那一份状态：项目在做什么、"
            "定了什么、谁在负责。**你观察到「所有人都该知道」的事实，写进它**"
            "（`cheese remember --everyone <事实>`），不要记进只有你自己读得到的"
            "记忆池。\n" + overview_doc
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
                "用 `cheese recall --query <关键词>` 查一次。"
                "**一次没查到不等于没有**：换个说法、或只用其中一两个关键词再试一次。"
            )
        if memories_core_omitted:
            # Core is the layer that is supposed to be unconditional. If even
            # it had to be cut, saying so is the only way it gets pruned.
            block += (
                f"\n\n> ⚠️ **核心记忆超预算了**：有 {memories_core_omitted} 条核心记忆"
                "没放下。核心记忆本该每轮全在场，出现这种情况说明它被当成普通记忆写"
                "了——挑几条降级成普通记忆（`cheese remember` 不带 `--core`）。"
            )
        parts.append(block)
    if session_opening:
        # 会话开场，不是本轮：这两条一次写对就一直对（机器多大不会变；上次的清单
        # 是给「不在场的那一轮」看的，会话活着的时候它自己的历史就是答案）。会变的
        # 东西不在这里 —— 它们在变的那一刻写成平台提醒，跟着下一轮的消息进来。
        parts.append(
            "## 这个会话开场时的运行环境（平台元信息，非用户输入）\n"
            + "\n".join(session_opening)
        )
    return "\n\n".join(parts)


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
        "看着办——能替它答的当场答，要接着干的照原来的简报重起一个分身，新分身的"
        "prompt 里照旧写这条活的线程标识。"
        "回话说在这条活上（`cheese tell <这条活> <说明>`），别只在房间里说，"
        "问话的人看的是那边。"
    )


PLATFORM_NOTICE = "【平台】以下是平台自动发出的指令，不是任何人手打的话："


def platform_prompt(content: str) -> str:
    return f"{PLATFORM_NOTICE}\n{content}"


def publication_prompt(content: str) -> str:
    """Carry the chat contract on new and resumed terminal input alike."""
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
