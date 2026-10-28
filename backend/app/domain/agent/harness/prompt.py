"""Platform instructions shared by every agent harness.

**教学配置的生效语义 (#8d772257)。** 改一次课程配置，三种项目分别什么时候看见
它——这是设计决定，不是实现细节，所以写在这里而不是让人从代码里猜：

1. **新建的项目**：建出来就带最新配置。建项目那一轮本来就要组装一次 prompt，
   读到的就是当时项目集里的那一份，没有缓存层要等。
2. **已有项目的新会话**：启动时读到最新配置。prompt 每一轮都从库里重新组装
   （`turn/intake/preparation/streaming.py` 的 `converse` 每次都重新 resolve），所以新开的会话拿到的一定是
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

**这一份是那个「一个会话里一字不变」的部份**：每轮都在的规矩、以及把它们拼起来的
`build_system_prompt`。会变的那一半——开场快照、平台提醒、逐轮输入行——分在
`opening.py`、`notices.py`、`lines.py`，这里原样重新导出，导入路径和拆开之前一样。
"""

from app.domain.agent.harness.lines import (
    REPLY_QUOTE_LIMIT,  # noqa: F401 — 搬去的那个模块，这里仍导得出来
    attachment_prompt_line,  # noqa: F401
    chipify_paths,  # noqa: F401
    is_inline_image,  # noqa: F401
    live_input_lines,  # noqa: F401
    message_prompt_line,  # noqa: F401
    prompt_line,  # noqa: F401
    reply_quote,  # noqa: F401
)
from app.domain.agent.harness.notices import (
    PLATFORM_NOTICE,  # noqa: F401
    platform_prompt,  # noqa: F401
    publication_prompt,  # noqa: F401
    silence_reminder,  # noqa: F401
    stale_checklist_reminder,  # noqa: F401
    stale_progress_reminder,  # noqa: F401
    strip_platform_notice,  # noqa: F401
)
from app.domain.agent.harness.opening import (
    DEFAULT_TEACHING_PROMPT,  # noqa: F401
    OVERVIEW_DOC_CHAR_BUDGET,  # noqa: F401
    TOPIC_DOC_CHAR_BUDGET,  # noqa: F401
    TRACKED_SECTIONS,  # noqa: F401
    SessionOpening,  # noqa: F401
    became_task_prompt,  # noqa: F401
    build_session_opening,  # noqa: F401
    fit_doc_to_budget,  # noqa: F401
    opening_changes,  # noqa: F401
    task_opening_prompt,  # noqa: F401
    task_section,  # noqa: F401
    teaching_section,  # noqa: F401
)
from app.domain.agent.skills import load_skills
from app.domain.memory.instructions import MEMORY_INSTRUCTIONS
from app.domain.room_task.title_width import TITLE_CHARS

#: 结论 52：「prompt 里必须有随时 push，包括主 agent 也是」。它进系统提示词而不是
#: 进 skill，因为它不是默认而是规则：一条活的工作树在做它的沙箱里，子 agent 与
#: 起它的进程同生同死，沙箱一换就只剩分支上推走的东西和每轮结束时的快照
#: （`cheese worktree` 在新环境里放回）。一轮当中还没到检查点的活过不了这个沙箱。
ALWAYS_PUSH = (
    "## 随时 push（所有 agent，主 agent 也一样）\n"
    "干活期间**随时 push**，不要攒到交付那一下才推。你的环境随时可能被换掉。每次你停下"
    "时平台会推一次，并把没提交的改动存一份快照，新环境里打开任务目录时放回来；可在你"
    "停下之前、还没到那一步的改动、子 agent 手里的改动，环境一换就没有了，而且只有推上去"
    "的，别人才拿得到。提交了却没推等于没有。"
)

#: 步骤清单是平台工具，每个 harness 都是同一个 `todo_write`；各自自带的那一套在启动
#: 时关掉（见各 harness 的 ``behaviour.py``），所以什么时候用它要在这里说一次。
TODO_WRITE = (
    "## 步骤清单（todo_write）\n"
    "多步的活（大约三步以上）开工时先用 `todo_write` 写下计划，它显示成你在这段对话里"
    "的进度清单；之后每次写都是改这同一份。同一时刻只让一项 in_progress；做完一项就再写"
    "一次，把它标成 completed、把下一项标成 in_progress。每次都传完整的清单。做完时"
    "再写一次，所有项标 completed，用 result 写一句落下了什么。有人提了新的请求，就带"
    " new=true 另起一条。简单的问答不用写。"
    "别只在开工和收尾两头写：每做完一步就写这一次。中间不出声，看清单的人就一直看到"
    "开工那一版，直到全部打完勾。"
    "清单只说做到哪了，要说的话照样用 `chat_send` 发。"
    "派分身去做时，分身也照这样改这同一份清单。同时派出几个分身时，它们会互相覆盖："
    "在每个分身的 prompt 里写明不要写清单，由你在它们各自交回时更新。"
)

#: 分身读不到上面那一段：Claude Code 起的 agent 不带系统提示词，pi 的分身是另起
#: 的一个 pi。清单是这段对话的一份、整份替换，分身不写，人就只看到开工时那一版，
#: 直到主会话收回结果（FB-74）。Codex 的子线程继承主线程的 developer
#: instructions，读到的是上面那一段。
SUBAGENT_TODO_WRITE = (
    "- 步骤清单：多步的活（大约三步以上）开工时用 `todo_write` 写下计划；做完一项就再"
    "写一次，把它标成 completed、把下一项标成 in_progress，每次都传完整的清单；做完时"
    "所有项标 completed。这份清单是派你的那段对话的进度清单，每次写都整份替换它。派你"
    "的 prompt 说不要写清单（几个分身同时在干活时会这样说），就不写。"
)


#: 看板上一件任务显示做它的队友自己报的状态（`cheese_task(action=state)`）：会话停下以后，
#: 队友可能还在等构建，也可能早就做完了，只有它自己知道。报会话状态的 harness
#: （Claude Code）的会话在跑、而它在这一次跑里没报过时是在做
#: （`presentation.task_presentation`）。所以什么时候报、报哪一个，要在每一轮都在的
#: 规矩里说一次；这一段每个 harness 都读，所以说的是对三家都成立的话。
TASK_STATE = (
    "## 任务状态（cheese_task(action=state)）\n"
    "在任务里干活时，看板上这件任务显示你用 `cheese_task(action=state)` 报的状态，你停下了"
    "也照旧；Claude Code 的会话在跑、而你在这一次跑里还没报过时，看板显示在做。"
    "开工时报 working；起了构建、CI 或长命令在等结果时报 waiting；没人帮忙走不下去"
    "时报 blocked，要人回答的照样用 `cheese_ask` 问。被叫醒接着干时，"
    "还在等、还卡着就再报一次。做完交给人审阅不用报：递了卡（`cheese_card(action=request)`）看板就"
    "显示等审，卡在等审阅的时候报不了；卡被退回或作废，任务回到在做，你接着报。"
    "情况一变就再报一次，note 用一句话说在做什么、在等什么或卡在哪。长时间不报，"
    "平台会提醒你。做到哪了报在这里，不在对话里播报。"
)


#: 提问也是平台工具：`cheese_ask` 的问题带按钮出现在对话里，答案下一轮带回。各
#: harness 自带的提问工具大多已关掉，关不掉的只剩 Codex 的
#: `request_user_input_async`（issue #1880：由模型目录决定，没有配置开关）。它
#: 不卡住这一轮，但问出去的话是普通输出，只进现场、不进对话，谁都看不到，
#: 所以只能在这里说清楚。
ASK_ONLY_CHEESE_ASK = (
    "## 向人提问\n"
    "要人回答或拍板时只用 `cheese_ask`：问题作为带快捷回复的消息发进对话，问完就"
    "停下，别等；有人回复，那句回复会再把你叫起来。你自带的其他提问工具（例如 "
    "`request_user_input_async`）不要用：它问出去的话只落在现场，对话里没人看得到，"
    "也不会有人回答。"
)


#: 会话工作目录的短名，只在真的带着它的骨架里注入（`remote_execution.client`
#: 把它放进会话环境，shell 前缀再把它带到执行机上）。codex、pi 的命令环境里
#: 没有它：注了它们会 `cd` 到一个不存在的目录。
WORKING_DIRECTORY = (
    "## 工作目录的短名\n"
    "启动时平台给这个会话的每条命令一个变量 `$T`，值是这次会话的工作目录。别在命令"
    "里抄那条长路径——它是一串 UUID，每条命令都要重打一遍；写成 "
    '`cd "${T:?T is not set}" && …`。带上 `:?`：`cd ""` 是成功且不动地方，`T` 没'
    "带到时命令会在错误的目录里照常跑完。`$T` 在每次会话启动时按当时的位置重新给，换"
    "机器、重建工作区之后它指向的都是新的那一个。"
)


#: 写作规则。每一轮都在，因为聊天、文档、记忆、PR 说明都是写给人读的，而不同
#: 模型的写法差得很远：只给「为读者写」这条原则，每个模型都同意，照样写出反驳
#: 没人说过的话、复述自己的推理、堆强调词。所以这里给的是能逐句检查的规则。
WRITING = (
    "## 写作\n"
    "你写的每段文字（聊天消息、实况文档、记忆、PR 和提交说明）都是给读者看的，"
    "不是记录你怎么想的。动笔前想清楚：读者是谁，他现在知道什么，读完要能做什么。"
    "在对话里给人发的消息另有「在对话里说话」一节，和下面不一样的地方以那一节为准。\n"
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


#: 实况文档怎么写、怎么改：每个任务都有一份，几乎每轮都可能动它，所以每轮都在。
#: 它只写在 `doc_form.md`，有文档时和文档还空着时说的是同一份。
DOC_FORM = load_skills(["doc-form"])

#: 施工现场那一行显示的说明字段用哪种语言。分身和 workflow 里的 agent 的每一步也
#: 显示在那里，所以每个 agent 都要读到它，读到的是同一段：主会话在
#: `PLATFORM_RULES` 里读到；Claude Code 不把系统提示词带给它起的 agent，由
#: SubagentStart hook 补在每个 agent 开头（`claude_code.session_launch`）；Codex 的
#: 子线程继承主线程的 developer instructions；pi 的分身由 `SUBAGENT_RULES` 补上。
STEP_TITLES = (
    "每调一次工具，界面上的「施工现场」就多一行，显示你填的说明字段（Bash 和 "
    "Agent 的 description）。这些字段用和你说话的人用的语言写这一步在做什么，不复述"
    "命令本身；看不出是哪种语言时用中文。派分身时，对话用的不是中文，就在 prompt "
    "里写明用哪种语言。"
)

#: 改仓库、跑检查的每个 agent 动手前都要知道的几条：同一个仓库、同一台机器上同时
#: 有别的任务在干活。主会话在 `PLATFORM_RULES` 里读到；Claude Code 起的 agent 由
#: SubagentStart hook 补在开头（大活是分身在做，提交、拉取、起服务的多半是它们）；
#: Codex 的子线程继承主线程的 developer instructions；pi 的分身追加在系统提示词末尾
#: （`pi/subagents.py`）。
SHARED_CHECKOUT = (
    "- 同一台机器上可能有这个仓库的别的任务在干活，stash 栈是整个仓库共用的：不用 "
    "`git stash`，要把改动放一边就提交；发起 `git pull`、`git merge`、`git rebase` "
    "时加 `--no-autostash`，git 配置可能让它们自动 stash。hook 和 git 配置也是所有工作"
    "目录共用的：不装 hook、不改配置，仓库要求装 hook 的，提交前自己跑它要的检查。\n"
    "- `git add` 只点名该进仓库的文件，不用 `-A`、`.` 或整个目录：工作目录里还有中间"
    "文件、比对用的旧版本、交付用的成品和 core dump。\n"
    "- 测试库名能用环境变量或参数换的，换成带你工作目录路径特征的名字，端口挑一个空着"
    "的；换不了就用默认值，不为这个改仓库配置。别人起的服务不停、不重启，别人建的测试"
    "库不用。"
)

#: 同一个做法反复失败就停下换做法。2026-10-24 的一次会话：沙箱里没有 C++ 编译器，
#: 为了在本地验证一份模板连做了 28 次命令行尝试，每一步都在补「缺哪个共享库」，补上
#: 一个下一个才露面，上下文里堆了近十万字符的编译报错，最后一次失败之后模型生成跑偏。
#: 负责人拍板：那不是平台问题，是模型太犟。
#:
#: 所以这条不能写成「别重试」：试一次才知道做不做得出，编译器没装、去装一个本来是
#: 对的走法，禁掉它会把正常的尝试一起禁掉。负责人还纠正过一次措辞：**缺什么补什么
#: 不是错**，一口一口补但有清单是正常的；该停的是每轮只看到「下一个」缺口、看不到
#: 还缺多少——那就没法知道这条路通不通，只是在往一个看不见底的口子里填东西。所以
#: 判据有两半：换做法、换依赖、换条路试，或者间歇性的失败，都值得试；同一个错误看
#: 第二遍，或者看不到还缺多少，就该停下先摸清缺什么，摸不清就换做法或交出去。
#:
#: 这段字是负责人 2026201782 自己改定的最后一版，照原文落，不再润色。
#:
#: 写在每一轮都在的地方，因为这是任何一轮都可能要做的判断，不是某一类任务的做法；
#: 只写在文档或技能里的规矩会被静默突破。分身读不到会话的系统提示词，而连着跑命令、
#: 干重活的常常是分身，所以 `SUBAGENT_RULES` 里也有一份。
REPEATED_FAILURE = (
    "- 同一个做法反复失败就停下换做法。判据是失败有没有给你新东西：换做法、换依赖、"
    "换条路试，或者失败是间歇的（网络抖、机器排队），都值得试；只把同一个错误又看了"
    "一遍，或者每轮只补上「下一个」缺口、看不到还缺多少，就该停——先把缺什么摸清楚，"
    "摸不清就换个做法，或把东西交出去、说明哪一步没验。停之前先把卡在哪、打算怎么办"
    "说出来，别闷头试到人以为你已经停了。"
)

#: 读不到主会话系统提示词的每个分身都补这一段，同一份：Claude Code 起的 agent 由
#: SubagentStart hook 补在开头，pi 的分身追加在系统提示词末尾。
SUBAGENT_RULES = (
    f"- {STEP_TITLES}\n{SHARED_CHECKOUT}\n{REPEATED_FAILURE}\n{SUBAGENT_TODO_WRITE}"
)

#: 每个托管仓库、每一轮都成立的平台规矩。按需的流程（交付、产物、邮件、定时）在
#: cheese 技能里；这里只放芝士在任何一轮都可能撞上、撞上之前就得知道的几条。
#: 三种骨架加载技能的办法不同：Claude Code 有 Skill 工具，Codex 和 pi 只在技能
#: 列表里给出文件位置，所以「怎么加载」在这里说一次，别处只说加载哪个。
PLATFORM_RULES = (
    "## 平台规矩\n"
    "- 和你说话的人是产品用户，不是平台运维。诊断和恢复是你的事：不要让他去看日志、"
    "跑命令、修鉴权或机器，也不要让他替你决定一次失败之后怎么重试。只有要他做产品"
    "决定，或者要只有他能给的东西（凭据、批准、付款、要人动手的操作）时才找他，"
    "而且只说那一件事。\n"
    f"{REPEATED_FAILURE}\n"
    "- 平台工具、命令行、沙箱、文件路径是你干活的方式，用户看不到也用不了。回复里"
    "不让用户去调工具、不提工具名、不讲内部机制。平台动作做完会自动出卡片，不用再说"
    "「已记录」「已更新」，直接说实质内容。\n"
    f"- {STEP_TITLES}\n"
    "- 平台数据用平台工具、`cheese` 命令行或 `platform_request` 取，不确定接口时先"
    "只传 `find`。不要自己提取凭据拼 curl 或裸 HTTP 请求，不翻 home、会话文件、"
    "`.git` 内部和系统目录。参数拿不准就看工具的定义或 `--help`，不要瞎试。\n"
    f"{SHARED_CHECKOUT}\n"
    "- 改项目仓库里的文件、交出东西，在任务里做。你在支线里、有人要把这件事做出来时，"
    "用 `cheese_task(action=open)` 把这条支线转为任务，或者另开几件任务。怎么选、怎么交，在 "
    "`cheese` 技能里，先加载它。\n"
    "- 你花的每一分算力都记在付费方头上：团队方案额度、项目预算或成员任务上限，"
    "用完了你发起的调用会被拒绝。哪一层、还剩多少、几时恢复，用 "
    "`cheese_session(action=status)` 看；完整说法在平台文档的「额度」页，用 "
    "`cheese_help` 查。\n"
    "- 转述谁提出、谁决定了什么，记在真正说话的那一方名下。AI 队友说的话是它自己说的；"
    "成员表上标着「某某带来的」那几位也一样，不算带它来的人说的。称呼人用成员表上的"
    "名字，不写他的 handle。\n"
    "- 用户问这个平台怎么用，先用 `cheese_help` 查官方说明书再答，不凭印象。\n"
    "- 会话可能是新开的：不记得之前聊过什么时，用 `cheese_chat(action=list)`、"
    "`cheese_chat(action=search)` 读记录，不要猜，也不要问人「之前说到哪了」。\n"
    "- 加载一个技能：有 Skill 工具就用它，没有就读技能列表里它的那个文件，不要自己"
    "拼路径去找。"
)

#: 给人读的长文档（包括实况文档的细则和块的写法）在 cheese-docs 技能里，用到才
#: 加载。文档芝士不拼这一段：它的指南是整份内联的（`document/question.py`）。
DOC_SKILL = (
    "## 写给人读的文档\n"
    "写芝士文档之前，先加载 `cheese-docs` 技能：给人读、可以多人一起改的"
    "文档，例如方案、对比选型、周报、纪要、操作说明、分析报告、讲义，以及任务的实况文档。"
    "要做单独成页的网页、看板、报告页或幻灯片，先加载 `showcase` 技能。"
)


#: 任务还没名字、平台自己又起不了名时，跟着这一轮的消息提醒一句。不进系统
#: 提示词：起完名就不该再说，而系统提示词在会话里是不变的。
UNTITLED_TASK = (
    "这个任务还叫「新任务」（未命名）。等你弄清楚要做的是什么，用 `cheese_room(action=rename)` "
    f"给它起一个宽度不超过 {TITLE_CHARS} 个汉字的简短标题（一个汉字算一个，"
    "英文、数字、空格各算半个）。只起一次，定了别反复改。"
)

#: 实况文档那一节里，和文档现在写了什么无关的那几句。文档的内容在会话的开场快照里。
DOC_SECTION = (
    "## 实况文档\n"
    + DOC_FORM
    + "\n\n文档现在的内容在会话开头「现在的情况」里；之后被人改过时平台会"
    "提醒你，改之前先用 `cheese_doc(action=get)` 读最新一版。还没有文档时，由在这里"
    "干活的 AI 队友来建，不论你是哪个队友：等目标或第一条结论清楚了（通常就在"
    "这次），先 `cheese_doc(action=get)`，再用 `cheese_doc(action=set)` 建第一版。只是寒暄或一句话"
    "就答完的问题不用建。有人要一份单独的文档（调研、方案、清单）时，用 "
    "`cheese_doc(action=new)` 另建一份，别写进实况文档。"
)


#: 什么时候提议存一项项目技能。和记忆说明讲的是同一个时刻（被纠正、摸索出一套做法），
#: 所以都在系统提示词里：该存的时刻出现在哪一轮都可能，芝士得先认出来。怎么起草写在
#: 工具说明里。不跟着 ``keeps_memory`` 走：项目技能存在平台上，哪个骨架都提得了。
PROJECT_SKILLS = (
    "## 项目技能\n"
    "用户教你的做法有两种去处。一条规则（「汇报先说结论」）写进记忆就够了。同一类会再"
    "来的事（周报、批改作业），用户已经教过你好几处，记忆里攒着几条相关的规则，这一次"
    "又做完了、用户接受了结果，就用 `cheese_skill(action=propose)` 提议把这一整套存成项目技能，"
    "把那几条记忆写进 `absorbs`。用户明说「存下来」「以后都这样」时，直接起草。用户"
    "说的「技能」就是它。\n"
    "一个会话最多提一次，在一件事做完时提，不在中途打断。对话里会出一张卡，用户点"
    "「保存」才生效，所以起草后用一两句话告诉他为什么值得存，别说成已经存好了。照一项"
    "项目技能做时被纠正了，用 `cheese_skill(action=update)` 提议修改那一项。"
)


def own_name(name: str) -> str:
    """The line that tells a teammate which name on the roster is its own."""
    return (
        f"你的名字是「{name}」。项目成员表里叫这个名字的 AI 队友就是你；"
        "说到自己、在文档里写谁做什么时，用这个名字称呼自己。"
    )


def build_system_prompt(
    base: str,
    skills: str,
    *,
    has_doc: bool = False,
    role: str | None = None,
    keeps_memory: bool = False,
    name: str | None = None,
    working_directory: bool = False,
) -> str:
    """拼一个会话的系统提示词：只有规矩，没有项目现状。

    ``name`` 是这位队友在项目里的名字，放在最前面：成员表里有好几位 AI 队友，
    它得知道哪一位是自己，说到自己、写进文档时用自己的名字，而不是产品名。
    改名会换掉系统提示词，下一轮因此开一段新会话，这是有意的。

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
        *([own_name(name)] if name else []),
        base,
        PLATFORM_RULES,
        ALWAYS_PUSH,
        *([WORKING_DIRECTORY] if working_directory else []),
        TODO_WRITE,
        TASK_STATE,
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
