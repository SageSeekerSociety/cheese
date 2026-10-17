"""问模型的那一段：system 说什么、这一批旧记忆怎么摆进去。

和 `dream_prompt.py` 一样，这里只有字：没有一条判断、没有一次读取。`migration_plan`
判的是「这份计划搬不搬得动」，而「模型该按什么规矩给去处」是另一回事——规矩写在
`MIGRATION_SYSTEM` 里，改它的措辞不需要动任何一行校验。
"""

from app.domain.memory.files import INDEX_NAME, parse_index
from app.domain.memory.migration_sources import OldEntry

#: 一次问模型的旧记忆条数上限。超了分几次问——一个项目的旧记忆可能上百条，全塞
#: 进一次调用既慢又容易让模型在中段开始敷衍。**分批只影响「这一次看见哪些旧记
#: 忆」**：一个已经存在的文件在每一批里都看得见，所以「并进哪一条」在批与批之间
#: 是一致的；只有「这一批要新建的文件」下一批看不见，于是两批可能各建一个近似的
#: 新文件。这一条由 report 上的人复核（新文件多、名字近似的会被一眼看出来）。
MIGRATION_CHUNK = 60

MIGRATION_SYSTEM = """你在把一批旧记忆搬进一套基于文件的记忆里。旧记忆是一条条独立
的句子（「条目池」时代的形态），新记忆是一条记忆一个 markdown 文件、一个作用域一份
`MEMORY.md` 索引。

你要**为每一条旧记忆指定一个去处**，然后把它写成新记忆的样子。你不是在摘要，是在
归置：同一件事在旧记忆里往往有好几个说法，把它们并进同一条。

## 五个去处（五选一，每条都必须选一个）

- `team`：新建一条**整个项目共读**的记忆。新的正文写进 `body`，`description` 写
  一句能让索引读得懂的钩子。`type` 只能是 `project`（项目里正在进行的事，代码和
  git 里读不出来的）、`reference`（外部系统的入口）或 `feedback`（全项目都该遵守
  的做法）。
- `private`：新建一条**只属于某个人**的记忆。要写 `owner`（那个人）。`type` 只能
  是 `user`（这个人是谁、懂什么）或 `feedback`（他个人的表达偏好、对他的工作方式
  的确认）。
- `merge`：并进一条**已经存在**的记忆（`existing` 里那些）。写 `path` 指向它，
  正文写进 `body`。同一件事已经有那条记忆了就并进去，别新建近似的副本。
- `suggest`：只**建议**写进 `CLAUDE.md` 或 `SKILL.md`，写 `target` 和一句话。
  你改不了它们，这里只把话留下。
- `discard`：这一条不值得变成记忆（代码里读一眼就知道、已经过期、和别的重复）。

## 规矩

1. **关于某个人的东西永远不能进 team。** 从「关于某个人」的池子来的条目，去处只
   能是 `private` / `merge`（并进那个人的文件）/ `suggest` / `discard`。
2. **项目规矩进 team，个人偏好进 private。** 这是同一条规矩的另一面：不要因为
   「反正是表扬」就把某个人的偏好写成全项目的做法。
3. **相对日期换成绝对日期**再写（「上周」→ 具体日期）。写不清的就别写。
4. `body` 是可以独立读懂的正文：把 `**Why:**`（为什么）和 `**How to apply:**`
   （什么时候用得上它）一起写出来，不要只写 `what`。
5. 人名、文件名、命令原样保留，不要改写你不确定的标识符。

## 输出

只输出一个 JSON 对象，没有别的字：

```json
{"decisions": [
  {"source": "<旧记忆的 id，原样抄>",
   "destination": "team | private | merge | suggest | discard",
   "owner": "<private 的主人，别的时候空着>",
   "path": "<文件名，kebab-case，不带 .md>",
   "scope": "team | private",
   "type": "project | reference | feedback | user",
   "description": "<索引那行的钩子，一句话>",
   "body": "<正文>",
   "target": "<suggest 的落点：CLAUDE.md / SKILL.md>",
   "reason": "<一句话：为什么放这儿>"}
]}
```

一条旧记忆一条决定，**一条都不能漏**。`discard` 也要写，理由里说清为什么不要它。
"""


def migration_prompt(
    *,
    project_id: str,
    project_name: str,
    entries: list[OldEntry],
    owners: list[str],
    existing: dict[str, str],
) -> str:
    """问模型的那一段：旧记忆一张表、现在的树一张表、规矩在 system 里。"""
    lines = [
        f"项目：{project_name}（`{project_id}`）",
        "",
        "这个项目里出现过的人（`private` 的主人只能从这里面选）："
        + ("、".join(owners) if owners else "（还没有）"),
        "",
        "## 现在这棵树里已经有的记忆",
        "",
    ]
    # 先按索引拼一份「钩子」表：模型挑 merge 目标时看的是钩子那一句，而不是整份
    # 正文——把每个文件的正文都塞进 prompt，一次调用就要多付几万个字。
    hooks: dict[str, str] = {}
    for path, text in sorted(existing.items()):
        if not path.endswith(f"/{INDEX_NAME}"):
            continue
        prefix = path[: -len(f"/{INDEX_NAME}")]
        for entry in parse_index(text):
            hooks[f"{prefix}/{entry.path}"] = entry.hook
    listed = [
        f"- `{path}` — {hooks.get(path) or '（索引里还没有它）'}"
        for path in sorted(existing)
        if not path.endswith(f"/{INDEX_NAME}")
    ]
    lines += listed or ["（这棵树还是空的）"]
    lines += ["", f"## 要归置的旧记忆（{len(entries)} 条）", ""]
    for entry in entries:
        lines += [
            f"### `{entry.source_id}`（{entry.where}）",
            "",
            entry.content.strip(),
            "",
        ]
    lines += [
        "---",
        "",
        "给上面每一条旧记忆指定去处，输出那个 JSON。",
    ]
    return "\n".join(lines)
