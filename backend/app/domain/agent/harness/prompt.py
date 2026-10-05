"""Platform instructions shared by every agent harness.

**教学配置的生效语义 (#8d772257)。** 改一次课程配置，三种项目分别什么时候看见
它——这是设计决定，不是实现细节，所以写在这里而不是让人从代码里猜：

1. **新建的项目**：建出来就带最新配置。建项目那一轮本来就要组装一次 prompt，
   读到的就是当时项目集里的那一份，没有缓存层要等。
2. **已有项目的新会话**：启动时读到最新配置。prompt 每一轮都从库里重新组装
   （`room/turn.py` 的 `_assemble_turn` 每次都重新 resolve），所以新开的会话拿到的一定是
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

import hashlib
import re
import uuid
from dataclasses import dataclass

from app.domain.agent.skills import load_skills
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
    "要人回答或拍板时只用 `cheese_ask`：问题作为带快捷回复的消息发进房间，问完就"
    "结束这一轮，别等；有人回复，那句回复会开启你的下一轮。你自带的其他提问工具（例如 "
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
    "「一句话：」、每段开头加粗、破折号插话、排比；也不写铺垫、过渡和收尾总结"
    "（「下面介绍」「综上所述」）。删掉后意思不变，就是本来不该写。\n"
    "5. 写具体的：数字、人名、日期、文件，不写「若干」「相关方」「近期」。\n"
    "6. 用读者认识的词。不用自己起的叫法；代码里的名字只在读者要照着操作时才写；"
    "缩写第一次出现时解释。同一个东西始终用同一个词。\n"
    "7. 指代要清楚。「这条」「那次」只能指向一个东西；编号只在同一个列表里引用。\n"
    "8. 写短。说明句不超过 45 字，步骤句不超过 35 字；一段不超过 4 行，只讲一件事。"
    "步骤写成祈使句：「打开设置」，不写「用户需要打开设置」。\n"
    "9. 只写有根据的事。数字、人名、日期要在材料里找得到；"
    "没说谁做、何时做就写「未定」，不替人补原因、效果和保证。"
    "直接下判断，不逐句免责，同一个结论只写一次。\n"
    "写完逐句问：删掉这句，读者会少知道什么？答不出来就删。"
)


#: 实况文档怎么写、怎么改：每个房间都有一份，几乎每轮都可能动它，所以每轮都在。
#: 它只写在 `doc_form.md`，有文档时和文档还空着时说的是同一份。
DOC_FORM = load_skills(["doc-form"])

#: 施工现场那一行显示的说明字段用哪种语言。分身和 workflow 里的 agent 的每一步也
#: 显示在那里，所以每个 agent 都要读到它，读到的是同一段：主会话在
#: `PLATFORM_RULES` 里读到；Claude Code 不把系统提示词带给它起的 agent，由
#: SubagentStart hook 补在每个 agent 开头（`claude_code.session_launch`）；Codex 的
#: 子线程继承主线程的 developer instructions。pi 的分身没有带说明字段的工具。
STEP_TITLES = (
    "每调一次工具，界面上的「施工现场」就多一行，显示你填的说明字段（Bash 和 "
    "Agent 的 description）。这些字段用房间里的人说的语言写这一步在做什么，不复述"
    "命令本身；看不出是哪种语言时用中文。派分身时，房间说的不是中文，就在 prompt "
    "里写明用哪种语言。"
)

#: 每个托管仓库、每一轮都成立的平台规矩。按需的流程（交付、产物、邮件、定时）在
#: cheese 技能里；这里只放芝士在任何一轮都可能撞上、撞上之前就得知道的几条。
#: 三种骨架加载技能的办法不同：Claude Code 有 Skill 工具，Codex 和 pi 只在技能
#: 列表里给出文件位置，所以「怎么加载」在这里说一次，别处只说加载哪个。
PLATFORM_RULES = (
    "## 平台规矩\n"
    "- 房间里的人是产品用户，不是平台运维。诊断和恢复是你的事：不要让他去看日志、"
    "跑命令、修鉴权或机器，也不要让他替你决定一次失败之后怎么重试。只有要他做产品"
    "决定，或者要只有他能给的东西（凭据、批准、付款、要人动手的操作）时才找他，"
    "而且只说那一件事。\n"
    "- 平台工具、命令行、沙箱、文件路径是你干活的方式，用户看不到也用不了。回复里"
    "不让用户去调工具、不提工具名、不讲内部机制。平台动作做完会自动出卡片，不用再说"
    "「已记录」「已更新」，直接说实质内容。\n"
    f"- {STEP_TITLES}\n"
    "- 平台数据用平台工具、`cheese` 命令行或 `platform_request` 取，不确定接口时先"
    "只传 `find`。不要自己提取凭据拼 curl 或裸 HTTP 请求，不翻 home、会话文件、"
    "`.git` 内部和系统目录。参数拿不准就看工具的定义或 `--help`，不要瞎试。\n"
    "- 不用 `git stash`：整个仓库共用一个 stash 栈，你 pop 出来的可能是别的任务的"
    "改动。要把改动放一边就提交。也不写 `.git/hooks`、不改共享的 git 配置。\n"
    "- 改项目仓库里的文件、要交出任何东西之前，先开一条任务。怎么开、怎么交，在 "
    "`cheese` 技能里，开任务前先加载它。\n"
    "- 用户问这个平台怎么用，先用 `cheese_docs_search` 查官方说明书再答，不凭印象。\n"
    "- 会话可能是新开的：不记得之前聊过什么时，用 `cheese_chat_list`、"
    "`cheese_chat_search` 读记录，不要猜，也不要问人「之前说到哪了」。\n"
    "- 加载一个技能：有 Skill 工具就用它，没有就读技能列表里它的那个文件，不要自己"
    "拼路径去找。"
)

#: 给人读的长文档（包括实况文档的细则和块的写法）在 cheese-docs 技能里，用到才
#: 加载。文档芝士不拼这一段：它的指南是整份内联的（`document/question.py`）。
DOC_SKILL = (
    "## 写给人读的文档\n"
    "写方案、对比选型、周报、纪要、操作说明、分析报告这类给人读的文档之前，先加载 "
    "`cheese-docs` 技能。要做的是给人看的页面、看板、报告页、方案对比或图表这类能看的"
    "成品，先加载 `showcase` 技能。"
)


#: 话题还没名字、平台自己又起不了名时，这一轮的第一件事。它跟着这一轮的消息走，
#: 不进系统提示词：起完名下一轮就不该再说，而系统提示词在会话里是不变的。
UNTITLED_FIRST = (
    "本话题还叫「新话题」（未命名）。本轮的第一个动作，在说开场白、回复任何内容、"
    "调用任何其他工具之前，先根据用户的需求执行 `cheese_title` 起个不超过 12 字的"
    "简短标题，然后再照常回应、干活。起标题只是一次工具调用，几乎不花时间。只起一次，"
    "定了别反复改。"
)

#: 实况文档那一节里，和文档现在写了什么无关的那几句。文档的内容在会话的开场快照里。
DOC_SECTION = (
    "## 当前话题的实况文档\n"
    + DOC_FORM
    + "\n\n文档现在的内容在会话开头「本话题现在的情况」里；之后被人改过时平台会"
    "提醒你，改之前先用 `cheese_doc_get` 读最新一版。还没有文档时，由在这个话题里"
    "干活的 AI 队友来建，不论你是哪个队友：等话题的目标或第一条结论清楚了（通常就在"
    "当轮），先 `cheese_doc_get`，再用 `cheese_doc_set` 建第一版。只是寒暄或一句话"
    "就答完的问题不用建。有人要一份单独的文档（调研、方案、清单）时，用 "
    "`cheese_doc_new` 建在项目资料库里，别写进实况文档。"
)


#: 什么时候提议存一项项目技能。和记忆说明讲的是同一个时刻（被纠正、摸索出一套做法），
#: 所以都在系统提示词里：该存的时刻出现在哪一轮都可能，芝士得先认出来。怎么起草写在
#: 工具说明里。不跟着 ``keeps_memory`` 走：项目技能存在平台上，哪个骨架都提得了。
PROJECT_SKILLS = (
    "## 项目技能\n"
    "用户教你的做法有两种去处。一条规则（「汇报先说结论」）写进记忆就够了。同一类会再"
    "来的事（周报、批改作业），用户已经教过你好几处，记忆里攒着几条相关的规则，这一次"
    "又做完了、用户接受了结果，就用 `cheese_skill_draft` 提议把这一整套存成项目技能，"
    "把那几条记忆写进 `absorbs`。用户明说「存下来」「以后都这样」时，直接起草。用户"
    "说的「技能」就是它。\n"
    "一个会话最多提一次，在一件事做完时提，不在中途打断。房间里会出一张卡，用户点"
    "「保存」才生效，所以起草后用一两句话告诉他为什么值得存，别说成已经存好了。照一项"
    "项目技能做时被纠正了，用 `cheese_skill_update` 提议修改那一项。"
)


def build_system_prompt(
    base: str,
    skills: str,
    *,
    has_doc: bool = False,
    role: str | None = None,
    keeps_memory: bool = False,
) -> str:
    """拼一个会话的系统提示词：只有规矩，没有项目现状。

    骨架在进程启动时读它，进程空闲退出后用 ``--resume`` 接着原来的对话重新拉起时
    再读一次。所以它在一个会话里必须一字不变：变了，从变的那个字往后、连同整段
    对话历史，前缀缓存全部作废。会变的现状在 :func:`build_session_opening` 里，作为
    新会话的第一条消息送进去；之后变了什么，用平台提醒补（:func:`opening_changes`）。

    ``keeps_memory`` 说的是**这一轮跑的 harness 会不会把记忆文件对账回平台**
    （``Harness.keeps_memory``，调用方按当前骨架传入）。默认不注：记忆
    那一段讲的是「写进 `~/.cheese/memory/`，下一轮平台的那一份里有它」，而 codex、
    pi 没有这条回路——照说明书写下的文件永远同步不回来，agent 却以为自己在写项目
    记忆。
    """
    parts = [
        base,
        PLATFORM_RULES,
        ALWAYS_PUSH,
        TODO_WRITE,
        ASK_ONLY_CHEESE_ASK,
        WRITING,
        DOC_SKILL,
    ]
    if skills:
        parts.append(skills)
    if has_doc:
        parts.append(DOC_SECTION)
    if keeps_memory:
        # 记忆这一段是有意整份在场的（照搬 CC）：四类记忆是什么、什么不该写、写前
        # 查重、用前核对——它是这个机制的说明书，而 agent 只有读了它才知道第一条
        # 记忆该写成什么样，什么时候该记又可能出现在任何一轮。
        parts.append(MEMORY_INSTRUCTIONS)
    parts.append(PROJECT_SKILLS)
    if role:
        parts.append(f"## 你的专家角色\n{role}")
    return "\n\n".join(parts)


#: 开场快照里，会话期间变了要再告诉一次的那几段。实况文档不在里面：它被人改过时
#: 平台已经发一条「请重读」的提醒（`block/documents.py`）。教学配置也不在：一个会话
#: 有意保持开场那一份到下一次新会话（见模块说明）。运行环境只在开场时有意义。
TRACKED_SECTIONS = ("topics", "artifacts", "roster", "overview", "memory")


@dataclass(frozen=True)
class SessionOpening:
    """一个新会话开场时项目的样子，按段存着，好在之后比对哪一段变了。"""

    sections: dict[str, str]

    @property
    def text(self) -> str:
        if not self.sections:
            return ""
        return platform_prompt(
            "## 本话题现在的情况\n"
            "下面是这个会话开始时本话题和项目的情况，是平台给的，不是谁说的话。"
            "之后变了的部分，平台会在后面的消息里再告诉你。\n\n"
            + "\n\n".join(self.sections.values())
        )

    def digests(self) -> dict[str, str]:
        return {
            key: hashlib.sha256(self.sections[key].encode()).hexdigest()
            for key in TRACKED_SECTIONS
            if key in self.sections
        }


def opening_changes(opening: SessionOpening, told: dict[str, str] | None) -> str:
    """一条接着跑的对话在这一轮要补听的现状；什么都没变就是空字符串。

    ``told`` 是上次告诉它时每一段的摘要（:meth:`SessionOpening.digests`），只补
    和它不一样的那几段。没有记录（``None``）时整份都说：那是一条在系统提示词还
    带着现状时开的老对话，或者记录丢了，它手上的现状不知道是哪一刻的。一条新开的
    对话用不上这一段，它的第一条消息带着整份快照（``Opening.session_opening``）。"""
    if told is None:
        return opening.text
    changed = [
        text
        for key, text in opening.sections.items()
        if key in TRACKED_SECTIONS
        and told.get(key) != hashlib.sha256(text.encode()).hexdigest()
    ]
    if not changed:
        return ""
    return platform_prompt(
        "这个会话开始以后，下面这几项变了，以这里为准：\n\n" + "\n\n".join(changed)
    )


def build_session_opening(
    *,
    doc: str | None = None,
    memory: MemoryIndex | None = None,
    roster: list[dict] | None = None,
    topics: list[dict] | None = None,
    artifacts: list[dict] | None = None,
    overview_doc: str | None = None,
    environment: list[str] | None = None,
    teaching: TeachingContext | None = None,
    keeps_memory: bool = False,
) -> SessionOpening:
    """新会话第一条消息前面的那份现状：话题、产物、成员、总览、文档、记忆索引。"""
    sections: dict[str, str] = {}
    if teaching is not None and (section := teaching_section(teaching)):
        sections["teaching"] = section
    if topics:
        lines = "\n".join(f"- {t['title']}" for t in topics)
        sections["topics"] = (
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
        # 产物清单进开场，是为了让下一次交付点得准名字。怎么点名、about 怎么写，
        # 规则在 `cheese_accept_request` 的工具说明里（与代码同源），这里只给清单
        # 和一句指路；交合并的那条路上没有名字可写错，所以也不用嘱咐。
        head = "## 这个项目的产物清单（交出去的东西，一项一行）\n"
        if artifacts:
            lines = "\n".join(
                f"- 《{a['name']}》"
                + (f"　第 {a['version']} 版" if a["version"] else "　还没交付过")
                + (f"　{a['about']}" if a.get("about") else "")
                + f"　id={a['id']}"
                for a in artifacts
            )
            sections["artifacts"] = (
                head + "交出文件或地址时，交的是下面某一项的新一版就用 "
                "`artifact=<那一行的 id>`，是一样新东西就用 `new_artifact` 加 "
                "`about`；交合并的不用声明产物，交的是项目那个仓库。细则看 "
                "`cheese_accept_request` 的说明。"
                "\n\n" + lines
            )
        else:
            sections["artifacts"] = (
                head + "清单还空着。第一次交出文件或地址时，用 `new_artifact=<真名>` "
                "加 `about=<一句话>` 声明它；交合并的不用声明产物，交的是项目那个"
                "仓库。细则看 `cheese_accept_request` 的说明。"
            )
    if roster:
        lines = "\n".join(
            f"- {m['name']}（{_standing(m)}，handle: {m['handle']}）" for m in roster
        )
        sections["roster"] = (
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
        sections["overview"] = (
            "## 项目总览（全项目共看的那一份，不是本话题的）\n"
            "项目所有人和所有芝士共同看的就是它：项目是什么、现在在做什么、定了"
            "什么、谁在负责。它分三块，**只有第一块是写的**：\n"
            "- **① 项目是什么** —— 你和人写，正文只有这一块（到总览房间用 "
            "`cheese_doc_edit` 改根话题的实况文档）：目标、范围（做 / 不做）、"
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
        sections["doc"] = "## 实况文档现在的内容\n\n" + fit_doc_to_budget(
            doc,
            TOPIC_DOC_CHAR_BUDGET,
            full_read_hint="用 `cheese_doc_get` 读全文",
        )
    elif doc is not None:
        sections["doc"] = "## 实况文档现在的内容\n\n本话题还没有实况文档。"
    if keeps_memory and memory is not None and not memory.is_empty():
        index_text = "\n\n".join(
            f"### {section.label}（`{section.prefix}/`）\n{section.text}"
            for section in memory.sections
        )
        sections["memory"] = memory_block(index_text, memory.warnings)
    if environment:
        # 会话开场，不是本轮：机器多大不会变；上次的清单是给「不在场的那一轮」看
        # 的，会话活着的时候它自己的历史就是答案。
        sections["environment"] = "## 这个会话开场时的运行环境\n" + "\n".join(
            environment
        )
    return SessionOpening(sections)


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
