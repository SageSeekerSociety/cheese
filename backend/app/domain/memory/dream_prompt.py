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

from app.domain.prompts import Facts, Kind, render_system

#: team 那一次调用的系统提示词（`pieces/dream/team.md`）。一条记忆写成什么样
#: （`pieces/dream/memory_format.md`）两份调用都要自己说全：它们都没有系统提示词
#: 里「记忆」那一段。上限的数和 `files.py` 里平台收不收的那两个数是一回事。
TEAM_DREAM_SYSTEM = render_system(Kind.dream_team, Facts())

#: private 那一次调用的系统提示词（`pieces/dream/private.md`）。
PRIVATE_DREAM_SYSTEM = render_system(Kind.dream_private, Facts())


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
