"""Platform instructions shared by every agent harness.

**教学配置的生效语义 (#8d772257)。** 改一次课程配置，三种项目分别什么时候看见
它——这是设计决定，不是实现细节，所以写在这里而不是让人从代码里猜：

1. **新建的项目**：建出来就带最新配置。建项目那一轮本来就要组装一次 prompt，
   读到的就是当时项目集里的那一份，没有缓存层要等。
2. **已有项目的新会话**：启动时读到最新配置。prompt 每一轮都从库里重新组装
   （`chat._assemble_turn` 每次都重新 resolve），所以新开的会话拿到的一定是
   此刻的配置。
3. **运行中的会话**：**保持它启动时的那一份，直到下一次冷启动。** 这不是本模块
   的选择，是 Claude Code 的事实：harness 用 `--append-system-prompt-file` 把
   prompt 交给它，而它**在启动时读一次**那个文件
   （`claude_code/session_launch.py` 的模块注释）。文件每轮都重写，重写是
   给**下一次**冷启动看的。

   所以「第 3 周改成了第 4 周」这件事，一个正在跑的会话当天听不到。这是有意接受
   的：往一个已经跑起来、上下文里全是第 3 周内容的会话里塞第 4 周的范围，比让它
   按第 3 周做完这一轮更糟。要立刻生效就重开会话（换机器/重建屏幕都会重开）。

`prompt_text`（逐轮的用户消息）里**不放**教学上下文，这是第 3 条的实现保证：
它只走 system prompt 这一条路，没有第二条路能让它在会话中途变脸。
"""

import re
import uuid

from app.domain.block.models import BlockKind
from app.domain.block.quoted_context import quoted_context_prompt
from app.domain.memory.files_store import MemoryIndex
from app.domain.memory.instructions import MEMORY_INSTRUCTIONS, memory_block
from app.domain.task.teaching import TeachingContext

_BARE_PATH_RE = re.compile(
    r"(?<![\w/.&<-])((?:[\w.-]+/)+[\w-]+\.\w{1,8}(?::\d+(?:-\d+)?)?)(?![\w/])"
)


# How a roster row is in the project, in the words the agent reads. An external
# member is someone from outside the team taking part in this one project.
_STANDING = {"owner": "项目所有者", "team": "团队成员", "external": "外部成员"}


def _standing(member: dict) -> str:
    if member.get("agent"):
        return "AI 队友"
    return _STANDING.get(member.get("source") or "", "成员")


def chipify_paths(fact: str) -> str:
    return _BARE_PATH_RE.sub(r"<&\1>", fact)


def teaching_section(context: TeachingContext) -> str | None:
    """本周教学范围 —— 课程级的那一段，或者 None。

    **None 是默认，而且是字面意义上的「一个字都不加」。** 一个不是课程的项目
    没有教学安排，而「这周教到哪了」对它是一句废话：说了会让 agent 以为自己在
    一门课里。所以这里不是渲染一个空标题，是整段不存在（`build_system_prompt`
    对 None 直接跳过），而且配套的取数在 `task.teaching.for_project` 里同样没
    有发生——不取，不是取了再说空话。

    **位置在角色之后、技能之前。** 教学安排不是人设（那是角色），也不是工具箱
    （那是技能）。它约束的是「这一周该做什么」，得在读具体怎么做之前先立住。
    """
    teaching = context.teaching
    if teaching.is_empty:
        return None
    # 课程名和「第几周」都可能缺：一个项目集配了教学安排却没说自己叫什么，仍然
    # 要有一段读得通的话，而不是一个空括号。
    where = []
    if context.course:
        where.append(f"课程：{context.course}")
    if teaching.current_week is not None:
        where.append(f"第 {teaching.current_week} 周")
    parts = ["## 本周教学范围" + (f"（{' · '.join(where)}）" if where else "")]
    if teaching.system_prompt:
        parts.append(teaching.fill(teaching.system_prompt))
    if teaching.allowed_topics:
        parts.append(
            "本周只做这些：\n"
            + "\n".join(f"- {topic}" for topic in teaching.allowed_topics)
        )
    if teaching.avoid_in_code:
        # 说清「避开」是什么意思。不说的版本会被读成禁令，agent 于是绕开一个它
        # 本来可以用的写法去做同一件事——这一周的练习照样练不到点子上。
        parts.append(
            "**本周的课还没讲到这些**，代码里请避开（这一周的练习不是练它）：\n"
            + "\n".join(f"- {item}" for item in teaching.avoid_in_code)
        )
    if context.materials:
        parts.append(
            "### 本周课件\n"
            + "\n".join(f"- 《{m['name']}》{m['url']}" for m in context.materials)
        )
    if context.knowledge:
        # 只给名字和描述，正文留在知识库里按 id 取：一份上传可以有多大，不是这门
        # 课说了算的，prompt 不跟着别人的文件长。
        parts.append(
            "### 本周知识材料（要正文用 id 自己去取）\n"
            + "\n".join(
                f"- 《{k['name']}》"
                + (f"（id={k['id']}）" if k.get("id") else "")
                + (f" {k['description']}" if k.get("description") else "")
                for k in context.knowledge
            )
        )
    return "\n\n".join(parts)


#: 实况文档的注入预算（#1535 第 1 条）。无上限的文档注入是实测到 79K 字符失控的
#: 那个洞；帽子按块各戴各的，超了按丢弃顺序压缩，全文改走按需读取。
OVERVIEW_DOC_CHAR_BUDGET = 6000
TOPIC_DOC_CHAR_BUDGET = 6000

_DOC_HEADING_RE = re.compile(r"(?m)^#{1,6} .*$")
_TEMP_SECTION_RE = re.compile(r"临时|TODO|待办|草稿|暂定|scratch|todo", re.IGNORECASE)
_PROGRESS_SECTION_RE = re.compile(r"进展|进度|状态|日志|记录|下一步|本周|历史")


def _doc_drop_class(label: str) -> int:
    """0 = 先丢（临时/待办），1 = 次之（进展/记录），2 = 最后才动。"""
    if _TEMP_SECTION_RE.search(label):
        return 0
    if _PROGRESS_SECTION_RE.search(label):
        return 1
    return 2


def fit_doc_to_budget(text: str, budget: int, *, full_read_hint: str) -> str:
    """压一份实况文档进预算帽；没超帽就原样返回，一个字节都不动。

    超帽按小节整段丢，丢弃顺序写死（先丢什么是一份契约，不是启发式）：临时/待办
    先丢，进展记录次之，目标/约束/决策/约定最后才动；全丢完仍超帽，剩下的从尾部
    硬截。压缩过才附一行说明——原文多长、全文去哪读；静默截断读起来就是「全文就
    这么长」。纯函数：提示词每轮重写，同样的输入必须永远得到同样的输出。
    """
    if len(text) <= budget:
        return text
    original = len(text)
    headings = list(_DOC_HEADING_RE.finditer(text))
    segments: list[tuple[int, str]] = []
    if headings:
        preamble = text[: headings[0].start()].rstrip("\n")
        if preamble:
            # 标题前的无题开头按保留处理：它多半是目标/背景，宁可硬截也不先丢。
            segments.append((2, preamble))
        for i, match in enumerate(headings):
            end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            body = text[match.start() : end].rstrip("\n")
            segments.append((_doc_drop_class(match.group(0)), body))
    else:
        # 无题长文按段落切，段落的第一行就是它的判据。
        for para in (p for p in text.split("\n\n") if p.strip()):
            segments.append((_doc_drop_class(para.splitlines()[0]), para.strip("\n")))

    def joined(segs: list[tuple[int, str]]) -> str:
        return "\n\n".join(body for _, body in segs)

    kept = segments
    for drop_class in (0, 1):
        i = 0
        while len(joined(kept)) > budget and i < len(kept):
            if kept[i][0] == drop_class:
                del kept[i]
            else:
                i += 1
    body = joined(kept)
    if len(body) > budget:
        body = body[:budget]
    note = (
        f"\n\n> ⚠️ 本文档超预算已压缩：原文 {original} 字符，这里保留 {len(body)} 字符。"
        "丢弃顺序：临时/待办先丢，进展记录次之，目标/约束/决策/约定最后才动。"
        f"全文按需读取：{full_read_hint}"
    )
    return body + note


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

#: 步骤清单是平台工具，每个 harness 都是同一个 `todo_write`；各自自带的那一套在启动
#: 时关掉（见各 harness 的 ``behaviour.py``），所以什么时候用它要在这里说一次。
TODO_WRITE = (
    "## 步骤清单（todo_write）\n"
    "多步的活（大约三步以上）开工时先用 `todo_write` 写下计划，它在房间里发成你的一条"
    "清单消息；之后每次写都是改这同一条。同一时刻只让一项 in_progress；做完一项就再写"
    "一次，把它标成 completed、把下一项标成 in_progress。每次都传完整的清单。做完时"
    "再写一次，所有项标 completed，用 result 写一句落下了什么。有人提了新的请求，就带"
    " new=true 另起一条。简单的问答不用写。"
    "清单只说做到哪了，要说的话照样用 `chat_send` 发。"
)


#: 提问也是平台工具：`cheese_ask` 的问题带按钮出现在对话里，答案下一轮带回。各
#: harness 自带的提问工具大多已关掉，关不掉的只剩 Codex 的
#: `request_user_input_async`（issue #1880：由模型目录决定，没有配置开关）。它
#: 不卡住这一轮，但问出去的话是普通输出，只进现场、不进对话，房间里没人看得到，
#: 所以只能在这里说清楚。
ASK_ONLY_CHEESE_ASK = (
    "## 向人提问\n"
    "要人回答或拍板时只用 `cheese_ask`。你自带的其他提问工具（例如 "
    "`request_user_input_async`）不要用：它问出去的话只落在现场，房间里没人看得到，"
    "也不会有人回答。"
)


#: 写作规则。每一轮都在，因为聊天、文档、记忆、PR 说明都是写给人读的，而不同
#: 模型的写法差得很远：只给「为读者写」这条原则，每个模型都同意，照样写出反驳
#: 没人说过的话、复述自己的推理、堆强调词。所以这里给的是能逐句检查的规则。
WRITING = (
    "## 写作\n"
    "你写的每段文字（聊天消息、实况文档、记忆、PR 和提交说明）都是给读者看的，"
    "不是记录你怎么想的。动笔前想清楚：读者是谁，他现在知道什么，读完要能做什么。\n"
    "1. 先写结论。第一句就是读者最需要的那件事，理由和细节放在后面。\n"
    "2. 不反驳没人说过的话。读者心里没有的说法，不要先否定它再给答案。"
    "✗「原因不是一条，是三条叠在一起。」✓「原因有三条：……」"
    "只有两种情况写否定：有人确实这么说过（写明是谁）；"
    "读者自然会这么以为，并且会因此做错。\n"
    "3. 不写自己是怎么想到的。猜过什么、推翻过什么、先查了哪里，都不写，"
    "只写最后成立的。\n"
    "4. 不写强调和修辞：「真正的」「本质上」「说白了」「换句话说」「值得注意的是」"
    "「一句话：」、每段开头加粗、破折号插话、排比。删掉后意思不变，就是本来不该写。\n"
    "5. 写具体的：数字、人名、日期、文件，不写「若干」「相关方」「近期」。\n"
    "6. 用读者认识的词。不用自己起的叫法；代码里的名字只在读者要照着操作时才写；"
    "缩写第一次出现时解释。\n"
    "7. 指代要清楚。「这条」「那次」只能指向一个东西；编号只在同一个列表里引用。\n"
    "8. 写短。说明句不超过 45 字，步骤句不超过 35 字；一段不超过 4 行，只讲一件事。\n"
    "9. 只写有根据的事。数字、人名、日期要在材料里找得到；"
    "没说谁做、何时做就写「未定」，不替人补原因、效果和保证。"
    "直接下判断，不逐句免责，同一个结论只写一次。\n"
    "写完逐句问：删掉这句，读者会少知道什么？答不出来就删。"
)


#: 实况文档的五块模板：有文档时和文档还空着时说的是同一套，所以只写一份。
DOC_FORM = (
    "它是一页状态页，写给没看过聊天的人和下一轮的你。按这五块组织：\n"
    "- **目标** —— 为什么做、做到什么程度算完，≤3 句。\n"
    "- **现状** —— 一句话：到哪了、卡在哪。项目总览取的就是这一句。\n"
    "- **需要谁做什么** —— 每条是 @谁、做什么、怎么做。要人做的排在前面。\n"
    "- **已确定** —— 查清的事实和做出的决定，只收会影响后续做法的；"
    "每条一句结论加一句理由或依据。\n"
    "- **待决** —— 问题、可选做法、你的建议、由谁定。\n"
    "- **相关**（平台自动）—— 任务卡、PR、子话题，不用手写。\n"
    "变了就替换，做完的删掉，过程和证据细节留在聊天、任务卡和 PR 里。"
    "全文不超过 1500 字。\n"
    "用块让人一眼看清，写法照抄：\n"
    "- 每条事项、决定的结果或状态，句末跟状态标签："
    "`{✓ 已确认}` `{✗ 不做}` `{! 待定}`。\n"
    "- 最要紧的一条结论或风险放进提示框：第一行 `> [!IMPORTANT]`"
    "（风险用 `> [!WARNING]`），下一行 `> 正文`。全文最多一个。\n"
    "- 有先后的进展或排期用时间线：第一行 `:::timeline`，每项 `- 时间 | 标题`，"
    "说明另起一行缩进两格，最后一行 `:::`。\n"
)


def build_system_prompt(
    base: str,
    skills: str,
    doc: str | None,
    memory: MemoryIndex | None,
    role: str | None = None,
    roster: list[dict] | None = None,
    topics: list[dict] | None = None,
    untitled: bool = False,
    artifacts: list[dict] | None = None,
    overview_doc: str | None = None,
    session_opening: list[str] | None = None,
    stage_guide: str | None = None,
    teaching: TeachingContext | None = None,
    keeps_memory: bool = False,
) -> str:
    """拼这一轮的 system prompt。

    ``keeps_memory`` 说的是**这一轮跑的 harness 会不会把记忆文件对账回平台**
    （``AgentRuntime.keeps_memory``，调用方按当前 runtime 传入）。默认不注：记忆
    那一段（说明书 + L1 索引）讲的是「写进 `~/.cheese/memory/`，下一轮平台的
    那一份里有它」，而 codex、pi 没有这条回路——照说明书写下的文件永远同步不回
    来，agent 却以为自己在写项目记忆。索引同理：正文铺不下去，注入的也就只是一
    串指向不存在的文件的指针。
    """
    parts = [base]
    if untitled:
        # First in the prompt on purpose: naming the topic is the FIRST action
        # of the session — before the opening reply, before any other tool —
        # so the rail never shows a working-but-unnamed 「新话题」.
        parts.append(
            "## 本轮第一件事：先给本话题起名（先于一切）\n"
            "本话题还叫「新话题」（未命名）。**本轮的第一个动作**——在说开场白、"
            "回复任何内容、调用任何其他工具之前——先根据用户的需求执行 "
            "`cheese_title` 起个 ≤12 字简短标题，"
            "然后再照常回应、干活。"
            "这条优先于「先回应，再干活」：起标题只是一次工具调用，几乎不花时间。"
            "（只起一次，定了别反复改。）"
        )
    parts.append(ALWAYS_PUSH)
    parts.append(TODO_WRITE)
    parts.append(ASK_ONLY_CHEESE_ASK)
    parts.append(WRITING)
    if role:
        parts.append(f"## 你的专家角色\n{role}")
    if teaching is not None and (section := teaching_section(teaching)):
        parts.append(section)
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
            'path="/topics?project_id=<本项目 id>&topic=<本话题 id>")`\n'
            "拿到 id 后用 `<#id>` 就能精确引用任何一个话题（包括没列在下面的）。"
            "读项目级的清单（`/topics`、`/projects/<id>/tasks`、"
            "`weeklies`、`library`、`artifacts`）都要带 "
            "`topic=<本话题 id>` 点名你所在的位置，不带会 403——那不是没权限。\n" + lines
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
        # **清单空着的时候这一段以短指针出现。** 那是必须说话的那一次：一个交文件
        # 的项目，第一次交付只能新建，而它起的那个名字会留在清单上。指针只留三件
        # 不能少的——怎么新建、合并不用声明、细则去 cheese_accept_request 的说明看；
        # 整套说明跟着清单走，不跟着每一轮走（#1535：空清单也全量注入是指令:信息
        # 约 8:1 的那一处）。
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
            # 短指针的三个不能丢：新建要带 about、合并不用声明、细则的权威文本
            # 是 cheese_accept_request 的工具说明（与代码同源，不会过期）。
            parts.append(
                head
                + "清单还空着，这个项目一样东西都还没交出去过。第一次交出文件或地址，"
                "用 `new_artifact=<真名>` 加 `about=<一句话>`（说的是这东西本身，"
                "不是这一版做了什么）声明它；交出这次合并的**不用声明产物**——交的"
                "是项目那个仓库，平台自己认得出。写法细则看 "
                "`cheese_accept_request` 工具的说明。"
            )
    if roster:
        lines = "\n".join(
            f"- {m['name']}（{_standing(m)}，handle: {m['handle']}）" for m in roster
        )
        parts.append(
            "## 项目成员 & 怎么点名\n"
            "要让某人去做事/通知到他，**在他名字前加 @**（如 `@张衡`，名字用下表"
            "准确值）——平台会把它变成可点的「@张衡」链接并给他**强提醒**。"
            "只写名字而不加 @ 只是普通文字，不会通知。\n" + lines
        )
    if overview_doc:
        # 人和 agent 共同看的东西是文档，不是一个共享记忆池（结论 7）：每个项目
        # 有一份总览文档，每间房间都读到同一份，谁改了都留痕。
        #
        # 这一份**分三块，只有第一块是写的**（#1889 第 1 条）。②③ 由平台从结构
        # 化数据现拼，进不了文档正文：手抄一份进去，读的人读到的不是它，而抄的人
        # 会以为事情办完了。哪一块谁写必须在这里说清——不说清，写的人只会照旧把
        # 手抄的结论贴回来。
        #
        # 传进来的那一段已经按这个结构拼好了（`chat._project_overview`）：① 从总览
        # 文档里取，②③ 只在总览房间拼。帽子仍然戴在整段上，防的是一份还没按新
        # 结构写过的老总览——那时 ① 取不到，注入的就是全文。
        parts.append(
            "## 项目总览（全项目共看的那一份，不是本话题的）\n"
            "项目所有人和所有芝士共同看的就是它：项目是什么、现在在做什么、定了"
            "什么、谁在负责。它分三块，**只有第一块是写的**：\n"
            "- **① 项目是什么** —— 你和人写，正文只有这一块（到总览房间用 "
            "`cheese_doc_set` 整份更新根话题的实况文档）：目标、范围（做 / 不做）、"
            "对外口径，≤1500 字。它很少变，变了才改。\n"
            "- **② 现在在做什么 / ③ 已结束的话题** —— "
            "**平台从结构化数据现拼，不在文档正文里**。要改就改源头：话题本身、"
            "结掉的那张卡。往正文里抄一份，"
            "谁都不会读到它（本话题里没拼给你的那几块，自己查：`/topics`）。\n"
            + fit_doc_to_budget(
                overview_doc,
                OVERVIEW_DOC_CHAR_BUDGET,
                full_read_hint="在项目根话题里调 `cheese_doc_get` 读全文",
            )
        )
    if doc:
        # 话题文档的模板（#1889 第 2 条）。这五块不是格式洁癖：读者是**没参与过
        # 讨论的人**和下一轮的自己，「现在是什么情况」得一眼看得到。流水账、追加
        # 的「更正」、粘贴的原文，都要后来的人自己推断哪一版有效——那不叫文档，
        # 叫过程。
        #
        # 写入时的检查（`doc_checks`）只提醒不拦，所以这里说一次就够；两处说的是
        # 同一套要求，不另立一份声明。
        parts.append(
            "## 当前话题的实况文档（这是最新状态；用户可能编辑了它，"
            "请按它继续工作，并在状态变化时用 `cheese_doc_set` 更新它）\n"
            + DOC_FORM
            + fit_doc_to_budget(
                doc,
                TOPIC_DOC_CHAR_BUDGET,
                full_read_hint="用 `cheese_doc_get` 读全文",
            )
        )
    elif doc is not None:
        # 房间有文档位、只是还空着（`""`，区别于没有文档这回事的 None）。只在
        # 上面那一支里说「维护它」，等于把第一版留给模型自己悟：Claude 会悟，
        # Kimi / MiMo 近 30 天在本项目里一篇都没建过——文档谁来维护就取决于
        # 坐进房间的是哪个模型。所以空的时候也说，而且说清「什么时候」。
        parts.append(
            "## 当前话题的实况文档（还没有）\n"
            "本话题还没有实况文档。它是给没参与讨论的人和下一轮的你看的，"
            "由在这个话题里干活的 AI 队友维护——不论你是哪个队友。"
            "等话题的目标或第一条结论清楚了（通常就在本轮），先 `cheese_doc_get`、"
            "再用 `cheese_doc_set` 建第一版；之后状态变化时更新它。"
            "只是寒暄或一句话就答完的问题不用建。\n" + DOC_FORM
        )
    if keeps_memory:
        # 记忆这一段是有意整份在场的（照搬 CC）：四类记忆是什么、什么不该写、写前
        # 查重、用前核对——它是这个机制的说明书，而 agent 只有读了它才知道第一条
        # 记忆该写成什么样。索引（L1）跟着它走，正文留在会话目录里让它自己读。
        parts.append(MEMORY_INSTRUCTIONS)
        if memory is not None and not memory.is_empty():
            index_text = "\n\n".join(
                f"### {section.label}（`{section.prefix}/`）\n{section.text}"
                for section in memory.sections
            )
            parts.append(memory_block(index_text, memory.warnings))
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
        "回话说在这条活上（`cheese_tell`），别只在房间里说，"
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
            "分身向主 agent 回报。"
        )
    )


def strip_platform_notice(text: str) -> str:
    """Neutralize the platform marker inside HUMAN text, so a person cannot type
    a message that reads as a platform instruction. The marker is the one thing
    in the prompt that claims institutional authority, so it has to be
    unforgeable from the content side."""
    return text.replace(PLATFORM_NOTICE, "【平台·用户原文】")


def is_inline_image(mime: str | None) -> bool:
    """Only native raster formats belong in the model's image input.

    Unknown types and SVG stay files, readable on demand rather than encoded
    into every turn. Keep initial, live and textual routing on this rule.
    """
    return mime in {"image/png", "image/jpeg", "image/gif", "image/webp"}


def attachment_prompt_line(
    author: str,
    path: str,
    *,
    embeds_images: bool,
    mime: str = "",
    gone: bool = False,
) -> str:
    if gone:
        # The file was deleted after the message (see `offered_attachments`).
        return (
            f"[{author}] 发过一个文件 {path}，但这份文件已经不在了"
            f"（多半是从资料库里删掉了），这一轮读不到它的内容。"
            f"需要它的话，请对方重新发一份。"
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
    author: str, content: str, quoted_context: dict | None = None
) -> str:
    return f"[{author}]: {strip_platform_notice(content)}" + quoted_context_prompt(
        quoted_context
    )


def prompt_line(
    b,
    *,
    embeds_images: bool,
    replied=None,
    recipient: str | None = None,
    gone: bool = False,
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
    longer exists."""
    if b.kind == BlockKind.attachment:
        line = attachment_prompt_line(
            b.author,
            b.content,
            embeds_images=embeds_images,
            mime=b.mime_type or "",
            gone=gone,
        )
    else:
        line = message_prompt_line(
            b.author, b.content, (b.meta or {}).get("quoted_context")
        )
    if replied is None:
        return line
    return f"{line}\n{reply_quote(replied, recipient=recipient)}"


def live_input_lines(
    author, content, attachments, *, stored=None, replied=None, recipient=None
):
    """Live input reads the same saved quote and formatting as initial input."""
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
            author, f["path"], embeds_images=True, mime=f["media_type"]
        )
        for f in files
    )
    if replied is not None:
        lines.append(reply_quote(replied, recipient=recipient))
    return lines, [f for f in files if is_inline_image(f["media_type"])]
