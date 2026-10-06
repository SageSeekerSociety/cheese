"""dream 那一段提示词（照搬 Claude Code 2.1.283 的 dream 提示词，翻成中文）。

CC 的 dream 是「一次对记忆文件的反思式整理」，四个阶段：Orient（看看现在有什么）、
Gather recent signal（找这段时间新出现的、值得留下的事）、Consolidate（写进记忆
文件）、Prune and index（修剪，并把索引压回上限）。原文从本机的 CC 二进制里取：

    strings -n 20 /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe \
        > /tmp/cc.txt
    python3 - <<'EOF'
    ... 搜 "# Dream: Memory Consolidation" ...
    EOF

三段结构照搬：四阶段正文、team memory 那一段（「be conservative pruning team/」
「When unsure, leave it」）、以及「Reconcile memories against CLAUDE.md」那一段。
改的只有芝士这一侧的事实：

1. 记忆目录是 `$HOME/.cheese/memory/`，两个作用域各一层；CC 还有一份 logs/ 活动
   流，芝士的历史在房间时间线和实况文档里，所以 Phase 2 换成了「上一轮之后有动静
   的房间，它们的记录和实况文档」。
2. CC 的 dream 可以 grep 会话 transcript；芝士跑在项目工作机的沙箱里，手上只有当前
   这一份仓库和平台给的那一段输入，所以那一档换成「上面给的输入」。
3. 两条芝士自己的规矩，CC 没有：**private 的内容不许升级进 team**（CC 的对应物是
   「不要把你的个人记忆塞进 team/」，芝士更硬：这条在下面再说一遍，因为芝士的
   private 是「人 × 项目」，写错地方等于替某个人公开了他的偏好）；**和仓库的说明
   文件冲突时只标注、不改它**（CC 原文就有，说的是 CLAUDE.md；托管的仓库不一定有这一
   份，所以这里让它自己去找仓库里实际有的那份）。

**这不是给某一个会话写的。** 它进的是平台起的那一轮整理，跑在项目默认芝士身上，
输入里带着 team 和各人的 private——所以「谁写的、写给谁看」在提示词里必须交代清楚，
否则一段 private 的偏好会被当成项目共识写进 team。
"""

from dataclasses import dataclass, field

#: 会话里那份记忆副本的根（和 `instructions.MEMORY_DIR` 同一个字符串）。
MEMORY_DIR = "$HOME/.cheese/memory"

#: 这一轮的工具约束。CC 的对应段叫 "Tool constraints for this run"，写在提示词里
#: 而不是只交给权限配置，是因为模型得先知道「这样做事会被拒」才不会把一轮整理
#: 花在试探上。芝士这里同样：只有读、以及记忆目录内的写与删。
TOOL_CONSTRAINTS = """**这一轮的工具约束：** 只有读，加上记忆目录内的写与删。
读是随便读的：`ls`、`find`、`grep`、`cat`、`head`、`tail`、`wc`、`stat` 这些只看
不动的命令，以及读仓库里的任何文件。写只允许落在记忆目录（`{memory_dir}/`）里——
改一个记忆文件、新建一个记忆文件、更新索引，都算；删除只允许删记忆目录里的 `.md`。
别的地方一行都不许动：改代码、改仓库里的说明文件、跑测试、装依赖、提交、推送，都会被拒。
这是整理记忆的一轮，不是干活的一轮。"""


#: 「Reconcile memories against CLAUDE.md」那一段，只给代码项目：文档项目没有仓库，
#: 也就没有说明文件可核。仓库里有哪一份、有没有，由它自己去看。
RECONCILE = """## 和仓库的说明文件核对

说明文件指 agent 在这个仓库里干活时会自动读进来的那几份：`CLAUDE.md`、`AGENTS.md`、
`.claude/rules/` 这一类，在根目录或子目录里；子目录里的那份只管它那一片。依赖目录
（`node_modules`、`.venv`）里的、个人的 `*.local.md` 都不算，README、`docs/` 这类写给人
看的文档也不算。最后的交代里写上核对了哪几份；一份都没有，就在交代里写一句在哪儿找过、没找到，
这一段其余跳过。

有的话先读一遍。对每一条讲 `feedback` / `project` 的记忆，检查它和说明文件在同一件事上
有没有冲突：

- **记忆旧了** —— 同一件事，说明文件和记忆说的是两套做法：说明文件是被维护、进了
  版本库的那一份。把记忆删掉，或者改成和它一致（`why` 还成立、`how` 已经不对的那
  一条，留住 `why`）。
- **说明文件可能旧了** —— 记忆明显晚于说明文件，而且是明确在纠正它：
  **这一轮不要改说明文件**，在那条记忆上标一句「和 <那份文件从仓库根算起的路径>
  冲突，需要确认哪个是现在的」，并写进你最后的交代里，让人去改。
- **不是冲突** —— 记忆只是比说明文件细，或者带着理由收窄了它一条规矩：别动。

"""


@dataclass(frozen=True)
class DreamBriefing:
    """这一轮 dream 的输入：索引、正文、以及上一次整理之后有动静的房间。"""

    #: 每个作用域的名字（`team` / `private/<handle>`）和它**完整的树**——
    #: 路径 → 正文，索引在 `MEMORY.md` 那一条里。整理要看得见正文才判得了
    #: 「这两条是同一件事」。
    scopes: dict[str, dict[str, str]] = field(default_factory=dict)
    #: 上次 dream 之后有新东西的房间：一行标题，加它的实况文档。
    rooms: list[str] = field(default_factory=list)
    #: 这是不是一个代码项目（有仓库）。有仓库时会请它找出仓库的说明文件、对着核对。
    code_project: bool = False

    def scope_names(self) -> list[str]:
        return sorted(self.scopes)


def _render_scope(name: str, files: dict[str, str]) -> str:
    if not files:
        return f"### {name}/\n（还没有任何记忆）"
    lines = [f"### {name}/"]
    for path in sorted(files):
        body = files[path].strip()
        lines.append(f"#### {name}/{path}\n{body}")
    return "\n\n".join(lines)


def _render_rooms(rooms: list[str]) -> str:
    if not rooms:
        return "（上次整理之后没有房间产生新内容）"
    return "\n\n".join(rooms)


def dream_prompt(briefing: DreamBriefing) -> str:
    """这一轮整理的全部输入，拼成一段。

    拼进来而不是让它自己去读：这些内容分布在数据库、别的房间和历史里，会话机上的
    那个沙箱一个都够不着；给它路径等于让它对着一份不存在的文件做整理。索引和正文
    都摊开，它要做的判断（合并、改名、删掉被推翻的那条）才有材料。
    """
    tree = "\n\n".join(
        _render_scope(name, files) for name, files in sorted(briefing.scopes.items())
    )
    repository = (
        "\n\n## 仓库\n这个项目有代码仓库，就在你当前的工作目录里。"
        if briefing.code_project
        else ""
    )
    phase4 = _PHASE4.format(reconcile=RECONCILE if briefing.code_project else "")
    return f"""# dream：整理记忆

你在做一次 dream —— 对记忆文件的一次反思式整理。把最近学到的东西提炼成持久、
有条理的记忆，让以后的会话一睁眼就能对上。

记忆目录：`{MEMORY_DIR}/`
这一轮要整理的树（`team/` 是全项目共看的，`private/<handle>/` 是某一个人和芝士
之间的那一份）：

{tree}

## 上一次整理之后有动静的房间

{_render_rooms(briefing.rooms)}{repository}

{TOOL_CONSTRAINTS.format(memory_dir=MEMORY_DIR)}

## 第一阶段 —— 摸底

- 看清上面那棵树现在有什么（下面已经把索引和正文都摊开了，不用再去读一遍）。
- 想清楚每一个作用域现在是围绕哪几件事在记：哪些文件其实是同一件事的两份，哪些
  文件是一件事被时间切成了好几条。

## 第二阶段 —— 找新信号

按优先级找**这之后**才出现、值得留下来的东西：

1. **上面那一段房间里发生的事** —— 新谈成的决定、被推翻的旧说法、改过的做法。
   一行一段地扫，别去仓库历史里翻第二遍。
2. **已经漂了的记忆** —— 和仓库现状对不上、或者和你手上这批输入对不上的那些。
3. **只在对话里出现过的事实** —— 有人说过、没有落到代码或文档里的约束、背景、
   外部入口。

不要为了找而找。只看你已经起了疑心、觉得可能有东西的那些地方。

## 第三阶段 —— 合并

值得记的每一条，在它自己的作用域目录下写成一个文件、或者改写已有的那个。写法用
系统提示词里「记忆」那一段的规矩（四种类型、正文结构、什么不该写）——那是唯一
的答案，这里不重复一遍。这一阶段要做的判断：

- **合并进已有的文件，而不是新建一个近似的副本。** 有同一个事的那条就改它。
- **相对日期换成绝对日期**（「昨天」「上周」→ `2026-09-27`），不然过一阵它就读不懂
  了。
- **删掉被推翻的说法** —— 今天这轮看出来它是错的了，就改在它自己的文件里，不要
  在旁边补一条「更正：」。

{phase4}"""


#: 第四阶段：修剪，以及把索引压回上限。CC 原文里这一段的标题是
#: "Phase 4 — Prune and index"。
_PHASE4 = """## 第四阶段 —— 修剪，并把索引压回去

把每个记忆文件的 frontmatter（`name`、`description`）改准、改到一行——以后每一轮
注入的索引就是从这两个字段拼出来的，一句过期的 `description` 就是一条过期的索引。

`MEMORY.md` 是**索引**，不是正文：一行一条，`- [标题](文件.md) — 一句钩子`，一行
控制在 150 字符以内，永远不要把记忆正文写进它。

- 删掉已经不成立、错了、被取代的那些指针，文件一起删。
- 长了的条目往它指的那个文件里搬：一行超过约 200 字符，说明它装着正文。
- 新变重要的事补一行指针。
- 两条互相矛盾的，改错的那一条。

{reconcile}## 关于 `team/`（比你自己那些文件要小心）

`team/` 是全项目所有人和所有芝士共看、共写的一份。别人也在往那里写：

- **第三阶段**：`team/` 内部的近似条目照样合并。如果一条 private 记忆只是把一条
  team 记忆又说了一遍，删掉那条 private 的。
- **第四阶段，修剪 `team/` 要保守**：
  - 确实被现在的代码推翻、或者被一条更新的 team 记忆标为已取代的：删，或者改对。
  - 只是你不认识、或者和你这段时间没关系的：**不要删**，别人可能在用它。
  - **拿不准就留着。** 一条过期的 team 记忆代价很小；删掉别人的一条承重笔记代价
    很大。
- **不许把 private 的内容升级进 team。** 这一轮不许做这个动作，不论它看起来多像
  一条项目规矩——那是本人自己决定的事，不是整理时顺手做的。private 那几棵树之间
  也不许互相搬。

把这一轮合并、更新、删掉了什么，写成一段简短的交代。什么都没改（本来就够紧）也
说一句。

"""
