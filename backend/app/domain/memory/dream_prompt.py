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
3. 芝士自己的一条规矩，CC 没有原样的：**和仓库的说明文件冲突时只标注、不改它**
   （CC 原文就有，说的是 CLAUDE.md；托管的仓库不一定有这一份，所以这里让它自己去找
   仓库里实际有的那份）。

**整理按作用域分开跑，提示词也是两份。** team 那一份（`dream_prompt`）进的是平台起
的那一轮会话，跑在项目总览房间的座位上：那个座位的会话、凭证和记录全项目都看得见，
所以它的输入只有共享的东西——team 的树和非私密房间的记录。private 那一份
（`PRIVATE_DREAM_SYSTEM` + `private_dream_prompt`）是平台对每一个人的 private 单独发的
一次模型调用：没有会话、没有工具、没有凭证，输入是这个人的树加 team 的索引，答回来的
是要怎么改，平台核过再写。「private 只是重复了 team 的那条就删掉」只在后一份里，因为
只有它同时看得见两边。
"""

from dataclasses import dataclass, field

#: 会话里那份记忆副本的根（和 `instructions.MEMORY_DIR` 同一个字符串）。
MEMORY_DIR = "$HOME/.cheese/memory"

#: 这一轮的工具约束。CC 的对应段叫 "Tool constraints for this run"，写在提示词里
#: 而不是只交给权限配置，是因为模型得先知道「这样做事会被拒」才不会把一轮整理
#: 花在试探上。芝士这里同样：只有读、以及记忆目录内的写与删。
TOOL_CONSTRAINTS = """**这次的工具约束：** 只有读，加上记忆目录内的写与删。
读是随便读的：`ls`、`find`、`grep`、`cat`、`head`、`tail`、`wc`、`stat` 这些只看
不动的命令，以及读仓库里的任何文件。写只允许落在记忆目录（`{memory_dir}/`）里——
改一个记忆文件、新建一个记忆文件、更新索引，都算；删除只允许删记忆目录里的 `.md`。
别的地方一行都不许动：改代码、改仓库里的说明文件、跑测试、装依赖、提交、推送，都会被拒。
这次是来整理记忆的，不是来干活的。"""


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
  **这次不要改说明文件**，在那条记忆上标一句「和 <那份文件从仓库根算起的路径>
  冲突，需要确认哪个是现在的」，并写进你最后的交代里，让人去改。
- **不是冲突** —— 记忆只是比说明文件细，或者带着理由收窄了它一条规矩：别动。

"""


@dataclass(frozen=True)
class DreamBriefing:
    """team 那一轮的输入：team 的树，以及上一次整理之后有动静的共享房间。"""

    #: team 这棵树：路径 → 正文，索引在 `MEMORY.md` 那一条里。整理要看得见正文
    #: 才判得了「这两条是同一件事」。
    team: dict[str, str] = field(default_factory=dict)
    #: 上次 dream 之后有新东西、且总览里的人都读得到的房间：一行标题，加它的发言。
    rooms: list[str] = field(default_factory=list)
    #: 这是不是一个代码项目（有仓库）。有仓库时会请它找出仓库的说明文件、对着核对。
    code_project: bool = False


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
    """team 这一轮整理的全部输入，拼成一段。

    拼进来而不是让它自己去读：这些内容分布在数据库、别的房间和历史里，会话机上的
    那个沙箱一个都够不着；给它路径等于让它对着一份不存在的文件做整理。索引和正文
    都摊开，它要做的判断（合并、改名、删掉被推翻的那条）才有材料。
    """
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
这次要整理的是 `team/`，全项目共看、共写的那一份：

{_render_scope("team", briefing.team)}

## 上一次整理之后有动静的房间

{_render_rooms(briefing.rooms)}{repository}

{TOOL_CONSTRAINTS.format(memory_dir=MEMORY_DIR)}

## 第一阶段 —— 摸底

- 看清上面那棵树现在有什么（下面已经把索引和正文都摊开了，不用再去读一遍）。
- 想清楚它现在是围绕哪几件事在记：哪些文件其实是同一件事的两份，哪些文件是一件
  事被时间切成了好几条。

## 第二阶段 —— 找新信号

按优先级找**这之后**才出现、值得留下来的东西：

1. **上面那一段房间里发生的事** —— 新谈成的决定、被推翻的旧说法、改过的做法。
   一行一段地扫，别去仓库历史里翻第二遍。
2. **已经漂了的记忆** —— 和仓库现状对不上、或者和你手上这批输入对不上的那些。
3. **只在对话里出现过的事实** —— 有人说过、没有落到代码或文档里的约束、背景、
   外部入口。

不要为了找而找。只看你已经起了疑心、觉得可能有东西的那些地方。

## 第三阶段 —— 合并

值得记的每一条，在 `team/` 下写成一个文件、或者改写已有的那个。写法用系统提示词里
「记忆」那一段的规矩（四种类型、正文结构、什么不该写）——那是唯一的答案，这里不
重复一遍。这一阶段要做的判断：

- **合并进已有的文件，而不是新建一个近似的副本。** 有同一个事的那条就改它。
- **相对日期换成绝对日期**（「昨天」「上周」→ `2026-09-27`），不然过一阵它就读不懂
  了。
- **删掉被推翻的说法** —— 这次看出来它是错的了，就改在它自己的文件里，不要
  在旁边补一条「更正：」。

{phase4}"""


#: 第四阶段：修剪，以及把索引压回上限。CC 原文里这一段的标题是
#: "Phase 4 — Prune and index"。
_PHASE4 = """## 第四阶段 —— 修剪，并把索引压回去

把每个记忆文件的 frontmatter（`name`、`description`）改准、改到一行——以后每次
注入的索引就是从这两个字段拼出来的，一句过期的 `description` 就是一条过期的索引。

`MEMORY.md` 是**索引**，不是正文：一行一条，`- [标题](文件.md) — 一句钩子`，一行
控制在 150 字符以内，永远不要把记忆正文写进它。

- 删掉已经不成立、错了、被取代的那些指针，文件一起删。
- 长了的条目往它指的那个文件里搬：一行超过约 200 字符，说明它装着正文。
- 新变重要的事补一行指针。
- 两条互相矛盾的，改错的那一条。

{reconcile}## 修剪 `team/` 要保守

`team/` 是全项目所有人和所有芝士共看、共写的一份，别人也在往那里写：

- 确实被现在的代码推翻、或者被一条更新的 team 记忆标为已取代的：删，或者改对。
- 只是你不认识、或者和你这段时间没关系的：**不要删**，别人可能在用它。
- **拿不准就留着。** 一条过期的 team 记忆代价很小；删掉别人的一条承重笔记代价
  很大。

把这次合并、更新、删掉了什么，写成一段简短的交代。什么都没改（本来就够紧）也
说一句。

"""


#: private 那一次调用的系统提示词。它没有会话、没有工具，也就读不到系统提示词里
#: 「记忆」那一段，所以一条记忆写成什么样在这里自己说全；四个阶段只留下用得上的
#: 那几步——它手上只有这个人的树和 team 的索引，没有房间、没有仓库。
PRIVATE_DREAM_SYSTEM = """你在整理一个人在一个项目里的私人记忆（private）：芝士和这个人之间
记下来的那一份，只有本人看得见。你只做整理，不新增输入里没有的事实。

一条记忆是一个 markdown 文件，开头是 frontmatter：

---
name: <短-kebab-case-slug>
description: <一行摘要>
type: user | feedback | project | reference
---
<事实本身；feedback 和 project 接着写 **Why:** 和 **How to apply:**>

`MEMORY.md` 是索引，不是正文：一行一条，`- [标题](文件.md) — 一句钩子`，没有
frontmatter。索引这一行就是记忆本身，写成照着就能做的一句主张。新写的索引行不超过
150 字符，一条记忆的正文不超过 1000 字；超了的那一版平台不收。

要做的判断：

- **合并**：几个文件说的是同一件事，合成一个，其余删掉。只合并同一件事。
- **相对日期换成绝对日期**（「昨天」「上周」→ 具体日期），日期从文件里读得出来才换。
- **删掉被推翻、被取代的那条**，不要在旁边补一条「更正：」。
- **只是把 team 里某一条又说了一遍的 private 记忆，删掉**：team 的那一条每次都在。
  下面给的 team 索引只用来判这件事，不要把 team 的内容搬进这里，也不要改 team。
- 每个文件的 `name`、`description` 改准、改到一行；索引和文件保持一一对应：删掉一个
  文件，同时删掉索引里指向它的那一行；改了 `description`，索引那一行跟着改。
- 拿不准就不动。本来就够紧，就什么都不改。

只输出一个 JSON 对象：

{"write": {"<文件名>": "<这个文件改完后的完整内容>", ...},
 "delete": ["<要删的文件名>", ...],
 "summary": "<一两句话：合并、改写、删掉了什么>"}

文件名就是下面列出的那些名字（`MEMORY.md`、`xxx.md`），不带目录。没写进 `write`、
也没列进 `delete` 的文件原样保留。"""


def private_dream_prompt(tree: dict[str, str], team_index: str) -> str:
    """一个人的 private 整理那一次调用的输入：他的树，加上 team 的索引。"""
    rendered = (
        "\n\n".join(f"### {path}\n{tree[path].strip()}" for path in sorted(tree))
        if tree
        else "（还没有任何记忆）"
    )
    index = team_index.strip() or "（team 还没有任何记忆）"
    return f"""## 这个人的 private 记忆

{rendered}

## team 的索引（只读，用来判哪些 private 只是重复了 team）

{index}"""
