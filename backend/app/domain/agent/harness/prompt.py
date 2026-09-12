"""Platform instructions shared by every agent harness."""

import re

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
    memories_core: int = 0,
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
            '`cheese title "<标题>"` 起个 ≤12 字简短标题，然后再照常回应、干活。'
            "这条优先于「先回应，再干活」：起标题只是一条命令，几乎不花时间。"
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
            '`cheese api GET "/topics?project_id=$CHEESE_PROJECT"`\n'
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
        core = [f"- {chipify_paths(m)}" for m in memories[:memories_core]]
        retrieved = [f"- {chipify_paths(m)}" for m in memories[memories_core:]]
        block = "## 项目记忆（你已知道的事实，回答时可引用）"
        if core:
            block += "\n\n### 核心记忆（每轮都在场，与本轮说什么无关）\n" + "\n".join(
                core
            )
        if retrieved:
            block += (
                "\n\n### 本轮检索到的记忆（按本话题/本轮消息挑出来的，**不是全部**）\n"
                + "\n".join(retrieved)
            )
        if memories_omitted:
            # 没注入必须可见: what did not come in is stated, never dropped in
            # silence. A reader who cannot tell "nothing was stored" from "this
            # turn did not ask for it" stops trusting memory entirely — and
            # stops asking for the part it can still get.
            block += (
                f"\n\n> ⚠️ 记忆池里还有 **{memories_omitted} 条这一轮没注入**"
                "（按与本轮上下文的相关性排的，排在后面的没进来；不是不存在）。"
                "**没列出来 ≠ 不存在**——换个话题、要用到某条旧约定或踩过的坑时，"
                '用 `cheese recall "<关键词>"` 现查；一次没查到也不等于没有，'
                "换个说法、用更短的词再试一次。"
            )
        if memories_core_omitted:
            # Core is the layer that is supposed to be unconditional. If even
            # it had to be cut, saying so is the only way it gets pruned.
            block += (
                f"\n\n> ⚠️ **核心记忆超预算了**：有 {memories_core_omitted} 条核心记忆"
                "没放下。核心记忆本该每轮全在场，出现这种情况说明它被当成普通记忆写"
                "了——挑几条降级成普通记忆（`cheese remember` 不加 `--core`）。"
            )
        parts.append(block)
    if turn_meta:
        parts.append(
            "## 本轮运行环境（平台元信息，非用户输入）\n" + "\n".join(turn_meta)
        )
    return "\n\n".join(parts)
