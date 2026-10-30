"""dream 的提示词：team 一份，private 一份，各是平台发的一次模型调用。

判断的部分照搬 Claude Code 2.1.283 的 dream 提示词（翻成中文）：合并同一件事的几份、
相对日期换成绝对日期、删掉被推翻的说法、把索引压回上限，以及 team 那一段「修剪
team/ 要保守」「拿不准就留着」。原文从本机的 CC 二进制里取：

    strings -n 20 /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe \
        > /tmp/cc.txt
    python3 - <<'EOF'
    ... 搜 "# Dream: Memory Consolidation" ...
    EOF

和 CC 不同的地方，都是芝士这一侧的事实：

1. **整理只整理，不提取**（#1336）。CC 的第二阶段从会话记录里找新信号；芝士的整理
   不读任何房间的发言，只对已有的记忆做合并、删除、改写。记下来只发生在当下，
   写的人手上的上下文比事后任何一次回看都全。
2. **一次调用，不是一轮会话。** 两份调用都没有工具、没有凭证、没有座位，答回来的
   是一份「写哪些、删哪些」的清单，平台核过再写（`dream.dream_answer`）。所以提示词
   里一条记忆写成什么样要自己说全（`MEMORY_FORMAT`），系统提示词里「记忆」那一段
   它读不到。
3. **一次只读一个作用域。** team 那一份只看 team 的树；private 那一份看这个人的树和
   team 的索引，而「private 只是重复了 team 的那条就删掉」只在后一份里，因为只有它
   同时看得见两边。
"""

#: 一条记忆写成什么样：两份调用都没有系统提示词里「记忆」那一段，所以在这里说全。
#: 上限的数和 `files.py` 里平台收不收的那两个数是一回事。
MEMORY_FORMAT = """一条记忆是一个 markdown 文件，开头是 frontmatter：

---
name: <短-kebab-case-slug>
description: <一行摘要>
type: user | feedback | project | reference
---
<事实本身；feedback 和 project 接着写 **Why:** 和 **How to apply:**>

`MEMORY.md` 是索引，不是正文：一行一条，`- [标题](文件.md) — 一句钩子`，没有
frontmatter。索引这一行就是记忆本身，写成照着就能做的一句主张。新写的索引行不超过
150 字符，一条记忆的正文不超过 1000 字；超了的那一版平台不收。"""

#: 共用的那几条判断（CC 第三、四阶段里用得上的部分）。
CONSOLIDATE = """要做的判断：

- **合并**：几个文件说的是同一件事，合成一个，其余删掉。只合并同一件事。
- **相对日期换成绝对日期**（「昨天」「上周」→ 具体日期），日期从文件里读得出来才换。
- **删掉被推翻、被取代的那条**，不要在旁边补一条「更正：」。
- 每个文件的 `name`、`description` 改准、改到一行；索引和文件保持一一对应：删掉一个
  文件，同时删掉索引里指向它的那一行；改了 `description`，索引那一行跟着改。
- 索引里一行超过约 200 字符，说明它装着正文：把正文搬回它指的那个文件。"""

#: 答案的形状（`dream.dream_answer` 照这个核）。
ANSWER = """只输出一个 JSON 对象：

{"write": {"<文件名>": "<这个文件改完后的完整内容>", ...},
 "delete": ["<要删的文件名>", ...],
 "summary": "<一两句话：合并、改写、删掉了什么>"}

文件名就是下面列出的那些名字（`MEMORY.md`、`xxx.md`），不带目录。没写进 `write`、
也没列进 `delete` 的文件原样保留。"""


#: team 那一次调用的系统提示词。
TEAM_DREAM_SYSTEM = f"""你在整理一个项目的共享记忆（team）：全项目所有人和所有芝士共看、
共写的那一份。你只做整理，不新增输入里没有的事实。

{MEMORY_FORMAT}

{CONSOLIDATE}

**修剪 team 要保守。** 别人也在往这里写：

- 被一条更新的 team 记忆标为已取代、或者两条互相矛盾而其中一条明显是旧的：删，或者改对。
- 只是你看不出用处、或者和别的记忆没关系的：**不要删**，别人可能在用它。
- **拿不准就留着。** 一条过期的 team 记忆代价很小；删掉别人的一条承重笔记代价很大。
- 本来就够紧，就什么都不改。

{ANSWER}"""


#: private 那一次调用的系统提示词。
PRIVATE_DREAM_SYSTEM = f"""你在整理一个人在一个项目里的私人记忆（private）：芝士和这个人之间
记下来的那一份，只有本人看得见。你只做整理，不新增输入里没有的事实。

{MEMORY_FORMAT}

{CONSOLIDATE}
- **只是把 team 里某一条又说了一遍的 private 记忆，删掉**：team 的那一条每次都在。
  下面给的 team 索引只用来判这件事，不要把 team 的内容搬进这里，也不要改 team。
- 拿不准就不动。本来就够紧，就什么都不改。

{ANSWER}"""


def _render(tree: dict[str, str]) -> str:
    if not tree:
        return "（还没有任何记忆）"
    return "\n\n".join(f"### {path}\n{tree[path].strip()}" for path in sorted(tree))


def team_dream_prompt(tree: dict[str, str]) -> str:
    """team 那一次调用的输入：team 的树，别的什么都没有。"""
    return f"## team 的记忆\n\n{_render(tree)}"


def private_dream_prompt(tree: dict[str, str], team_index: str) -> str:
    """一个人的 private 整理那一次调用的输入：他的树，加上 team 的索引。"""
    index = team_index.strip() or "（team 还没有任何记忆）"
    return f"""## 这个人的 private 记忆

{_render(tree)}

## team 的索引（只读，用来判哪些 private 只是重复了 team）

{index}"""
