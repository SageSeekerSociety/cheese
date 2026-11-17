"""What a session is told about its memory: the rules (`pieces/memory/`), the
index it opens with, and where one person's private index is.

The rules are Claude Code 2.1.283's memory section, translated, with three
changes for the platform: the scopes are `team/` and `private/<handle>/` and
follow the project, not a repository; what the platform already records as
structured data (progress, members, tasks, the living document) is not
written into memory; and `private/` exists only in one person's private chat.
"""

#: 会话里那份记忆副本的根，按 agent 在文件工具里该写的那个样子写：`~` 是会话
#: 自己的家，在会话机上，runner 就在那里对账（`runner.memory_root`）。不写成
#: `$HOME`：agent 会去问 shell，而 shell 跑在执行机上，答的是那台机器的家，写到
#: 那里的记忆永远收不回来。规矩正文（`pieces/memory/`）里的路径也是这一个。
MEMORY_DIR = "~/.cheese/memory"


def memory_block(index_text: str, warnings: list[str] | None = None) -> str:
    """注入的那一段：索引，加上被截断时的那句话。

    警告跟索引一起进去，不只在写入端出现：截断这件事发生在**读**的时候，读到
    一段短的索引却不知道它短了的人，会去改错地方。
    """
    parts = [
        f"## 你的记忆（索引；正文在 `{MEMORY_DIR}/` 下，需要时自己读）\n{index_text}"
    ]
    for warning in warnings or []:
        parts.append(f"> ⚠️ {warning}")
    return "\n\n".join(parts)


def private_index_pointer(owner: str) -> str:
    """这一轮那一位的 private 记忆在注入里的样子：不是索引本身，是它在哪。

    注入是会话的输入，输入原样记进会话记录，房间里的人都读得到；记录里哪一段是谁
    的，只能按平台自己记下的结构认，不能按文字里的记号认——文字谁都写得进去
    （team 的索引就是）。所以 private 索引不进输入，由 agent 自己读：那一次文件工
    具调用落在 `private/<他>/` 下，runner 给调用和它的结果记上主人
    （`cheese.private`），会话记录只给本人看（`session_record.privacy`）。"""
    path = f"{MEMORY_DIR}/private/{owner}/MEMORY.md"
    return (
        f"这一轮答的是 {owner}。他的 private 记忆只有他本人看得到，索引在 `{path}`："
        "这一轮动手之前先用 Read 读它，正文在同一个目录下，需要时再读。"
    )
