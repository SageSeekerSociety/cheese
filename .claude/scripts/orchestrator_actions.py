"""Extract action items from a retrospective's Markdown, without guessing fields.

A 3-hour retro's action items are the list the next retro opens by checking off
(总调度工作流 v2 §5 step 1), and orchestrator_metrics.py carries them out
verbatim as an 「行动项核对表」. The three fields that list asks every item to
name — owner, 落点层, 怎么核 — are read out of the item's own text and stay 「—」
when the item never wrote one, so a table built from a retro written before
those three were required still says which of them it is missing.

Usage: python3 .claude/scripts/orchestrator_actions.py --self-test   (offline)
"""

import re
import sys
from datetime import datetime, timezone

#: The sections an item list has been written under. 工作流 v2 §5 names it
#: 「行动项」; retros written before v2 landed called the same list 「改进」 or
#: 「下一步」, and the newest retro in the library is often one of those. The
#: first of these names the document has wins, so a retro that gained a
#: 「行动项」 section is read from there and never from the older one.
SECTIONS = ("行动项", "改进", "下一步")

#: A field label at the head of its own clause. The three fields a checked-off
#: item needs are written as clauses inside the item itself — `owner 芝士人，
#: 落点 L5，怎么核：文档里这一行改完。` — and older retros spell 落点层 as 落点
#: and owner as 负责人. A label has to start a clause (the item's first word, or
#: right after a separator) so that prose like 「根因落在哪一层」 does not donate
#: a `层` column to the row. The colon is optional: both `怎么核：…` and
#: `owner 芝士人` are how the retros write it.
_FIELD = re.compile(
    r"(?:^|[，,；;。、：])\s*(owner|负责人|落点层|落点|层|怎么核)\s*[:：]?\s*"
)

#: The columns the table prints, in order, and the label each one accepts.
COLUMNS = ("owner", "层", "怎么核")
_FIELD_KEY = {"负责人": "owner", "落点层": "层", "落点": "层"}

#: A 3-hour retro's title starts with this. The weekly one is 总调度周深度复盘
#: and carries its own list under its own step (§6), so it is not this one.
RETRO_TITLE = "总调度复盘 "

#: The instant a document row without a readable `created_at` sorts to. It can
#: never win the "newest" comparison.
_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)


def action_items(markdown):
    """Prefer 行动项; accept 改进 and 下一步 in older retrospectives.

    Source order is preserved: the next retro checks the items off in the order
    the last one wrote them, and a reordered list is a different list.
    """
    sections = []
    active = None
    level = 0
    for line in markdown.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            depth, title = len(heading[1]), heading[2].strip()
            if active is not None and depth <= level:
                active = None
            title = re.sub(r"^[\d一二三四五六七八九十]+[.、．：:]\s*", "", title)
            if title in SECTIONS:
                active = []
                level = depth
                sections.append((title, active))
            continue
        if active is not None:
            active.append(line)
    chosen = []
    for name in SECTIONS:
        chosen = [lines for title, lines in sections if title == name]
        if chosen:
            break
    items = []
    for lines in chosen:
        current = None
        for line in lines:
            bullet = re.match(r"^(?:[-*+]\s+|\d+[.)、]\s+)(.*)$", line)
            if bullet:
                current = re.sub(r"^\[[ xX]\]\s*", "", bullet[1]).strip()
                items.append(current)
            elif line.strip() and current is not None:
                items[-1] += " " + line.strip()
    return items


def fields_of(item):
    """One row's owner / 层 / 怎么核, each 「—」 where the item wrote none.

    A label's value runs from the label to the next label, with the clause
    separators taken off both ends. Nothing outside a label is read into a
    column: what the item says about the work stays in the 行动项 cell.
    """
    found = {}
    hits = list(_FIELD.finditer(item))
    for i, match in enumerate(hits):
        key = _FIELD_KEY.get(match.group(1), match.group(1))
        stop = hits[i + 1].start() if i + 1 < len(hits) else len(item)
        value = item[match.end():stop].strip().strip("。，,；;、").strip()
        if value:
            found.setdefault(key, value)
    return found


def action_table(markdown):
    """List the source items; absent owner/layer/check stay explicit as —."""
    items = action_items(markdown)
    rows = ["## 行动项核对表", "", "| 行动项 | owner | 层 | 怎么核 |",
            "| --- | --- | --- | --- |"]
    for item in items:
        fields = fields_of(item)
        cells = [item] + [fields.get(key, "—") for key in COLUMNS]
        rows.append("| " + " | ".join(cell.replace("|", r"\|") for cell in cells) + " |")
    rows.extend(["", f"行动项：{len(items)} 条"])
    return "\n".join(rows)


def _created(row):
    """Sort key for a document row's creation instant.

    The platform writes ISO-8601 with an offset; `Z` is folded to `+00:00` so
    the two spellings land on the same axis. A row with no readable timestamp
    sorts earliest and can never be picked.
    """
    try:
        raw = (row.get("created_at") or "").replace("Z", "+00:00")
        when = datetime.fromisoformat(raw)
    except ValueError:
        return _EARLIEST
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def last_retro(docs):
    """The newest 3-hour retro row in `docs`, or None when there is none.

    `docs` is the rows `GET /projects/{id}/documents` returns. Newest is
    newest `created_at`, not the order that endpoint lists in (latest changed)
    and not `updated_at`: a typo fixed inside an old retro must not promote it
    to the previous one.
    """
    best = None
    for row in docs:
        if not isinstance(row, dict):
            continue
        if not str(row.get("title") or "").startswith(RETRO_TITLE):
            continue
        if best is None or _created(row) > _created(best):
            best = row
    return best


def self_test():
    """Offline: no token, no network. Every input is a shape a real retro wrote.

    The first block is the 10-13 22:30 retro's 行动项 section as it stands —
    the `owner …，落点 …，怎么核：…` clause form, including an item that names
    no 怎么核 and one whose 落点 carries its own parenthetical. Before the field
    parser was taught those clauses it wanted a colon after every label and
    only ever accepted 层, so all three rows came out as three 「—」: the table
    looked complete and carried nothing.
    """
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    retro_10_13 = """## 数据

- 等采纳 11 次：p50 18 分。

## 行动项

- 改《总调度工作流 v2》第 2 节例外表的「连续失败」一行：平台限流先试着换到还能起会话的座位，换得动就换，全点不动才等。owner 芝士人，落点 L5，怎么核：文档里这一行改完。
- 接力手册里把「哪个座位还能起会话」记成一张常更新的对照表，换座位不现场试。owner 芝士人，落点 L5。
- 换完座位 30 分钟内确认新座位上真的在跑。owner 芝士人，落点 L0（扫描已有「最后 event 时间」字段）。

## 复盘的复盘

- 「采纳前读 PR 的 `mergeable`」——**做到了**：进了工作流 v2 第 3 节。
"""
    items = action_items(retro_10_13)
    check("只取行动项小节，别的小节不算", len(items), 3)
    check("三条按原文顺序", items[0].startswith("改《总调度工作流 v2》"), True)
    check("第二条原文带出，字段也留在原文里",
          items[1], "接力手册里把「哪个座位还能起会话」记成一张常更新的对照表，"
                    "换座位不现场试。owner 芝士人，落点 L5。")
    check("第三条原文带出", items[2].startswith("换完座位 30 分钟内"), True)

    check("字段齐全的那条三列都填上",
          fields_of(items[0]),
          {"owner": "芝士人", "层": "L5", "怎么核": "文档里这一行改完"})
    check("没写怎么核就空着，不从前后文猜",
          fields_of(items[1]), {"owner": "芝士人", "层": "L5"})
    check("落点带着自己的括注一起带出",
          fields_of(items[2]),
          {"owner": "芝士人", "层": "L0（扫描已有「最后 event 时间」字段）"})

    table = action_table(retro_10_13)
    check("表头四列", table.splitlines()[2], "| 行动项 | owner | 层 | 怎么核 |")
    check("缺的字段写 —，不空格",
          [line for line in table.splitlines() if line.startswith("| 接力手册")],
          ["| 接力手册里把「哪个座位还能起会话」记成一张常更新的对照表，换座位不现场试。"
           "owner 芝士人，落点 L5。 | 芝士人 | L5 | — |"])
    check("末尾是条数", table.splitlines()[-1], "行动项：3 条")

    # -- 小节叫什么，以及不同时取两份 --------------------------------------
    check("行动项优先于改进",
          action_items("## 改进\n\n- 旧的。\n\n## 行动项\n\n- 新的。\n"), ["新的。"])
    check("没有行动项就用改进",
          action_items("## 改进\n\n- 旧的。\n"), ["旧的。"])
    check("都没有就用下一步（10-14 03:57 那份就这么写）",
          action_items("## 这一段定了什么\n\n- 定了。\n\n## 下一步\n\n- 等 #1524 的完整 CI 出结果，"
                       "绿了采纳。\n"), ["等 #1524 的完整 CI 出结果，绿了采纳。"])
    check("标题带序号也认", action_items("## 2. 行动项\n\n- 一条。\n"), ["一条。"])
    check("同级标题收段，后面的条目不混进来",
          action_items("## 行动项\n\n- 一条。\n\n## 复盘的复盘\n\n- 不是行动项。\n"), ["一条。"])
    check("复选框剥掉，内容照抄",
          action_items("## 行动项\n\n- [ ] 要做的。\n- [x] 做完的。\n"), ["要做的。", "做完的。"])
    check("折行并进上一条，不新起一条",
          action_items("## 行动项\n\n- 第一条，\n  这半句接上。\n"), ["第一条， 这半句接上。"])
    check("小节是空的就报 0 条，不落到别处去凑",
          action_table("## 行动项\n\n## 改进\n\n- 别处的。\n").splitlines()[-1], "行动项：0 条")

    # -- 字段别认错 ---------------------------------------------------------
    check("句子里的「哪一层」不是层字段",
          fields_of("根因落在哪一层，下一步再定。"), {})
    check("负责人是 owner 的另一种写法",
          fields_of("做一件事。负责人 芝士S，落点层 L4，怎么核：跑一次自测。"),
          {"owner": "芝士S", "层": "L4", "怎么核": "跑一次自测"})
    check("冒号写法和不写冒号都收",
          fields_of("做一件事。owner：芝士S，落点：L4，怎么核：跑一次。"),
          {"owner": "芝士S", "层": "L4", "怎么核": "跑一次"})
    check("竖线在表里转义，不然断列",
          [line for line in action_table("## 行动项\n\n- 甲 | 乙。owner 张三。\n").splitlines()
           if line.startswith("| 甲")],
          ["| 甲 \\| 乙。owner 张三。 | 张三 | — | — |"])

    # -- 上一份复盘是哪一份 --------------------------------------------------
    def doc(title, created, changed=None):
        return {"id": title, "title": title, "created_at": created,
                "updated_at": changed or created}

    check("取最新创建的一份 3 小时复盘",
          last_retro([
              doc("总调度复盘 10-13 15:20–19:35（UTC）", "2026-10-13T19:39:53Z"),
              doc("总调度复盘 10-14 07:15–10:15（UTC）", "2026-10-14T10:18:56Z"),
              doc("总调度复盘 10-13 19:35–22:30（UTC）", "2026-10-13T22:35:40Z"),
          ])["title"],
          "总调度复盘 10-14 07:15–10:15（UTC）")
    check("周深度复盘不是这一份",
          last_retro([
              doc("总调度周深度复盘 2026-W41（10-06 至 10-13，UTC）", "2026-10-14T02:12:54Z"),
              doc("总调度复盘 10-13 11:30–12:00（UTC）", "2026-10-13T11:55:29Z"),
          ])["title"],
          "总调度复盘 10-13 11:30–12:00（UTC）")
    check("后来改过的旧复盘不会顶上来（比创建时间，不比修改时间）",
          last_retro([
              doc("总调度复盘 10-13 15:20–19:35（UTC）", "2026-10-13T19:39:53Z",
                  "2026-10-14T10:20:00Z"),
              doc("总调度复盘 10-14 07:15–10:15（UTC）", "2026-10-14T10:18:56Z"),
          ])["title"],
          "总调度复盘 10-14 07:15–10:15（UTC）")
    check("没有复盘时回 None，不留空表",
          last_retro([doc("总调度接力手册", "2026-10-11T15:03:54Z")]), None)
    check("时间戳写 Z 和写 +00:00 排在一根轴上",
          last_retro([
              doc("总调度复盘 A（UTC）", "2026-10-14T07:11:03+00:00"),
              doc("总调度复盘 B（UTC）", "2026-10-14T10:18:56Z"),
          ])["title"],
          "总调度复盘 B（UTC）")
    check("认不出的时间戳当最早，不崩也不抢",
          last_retro([
              doc("总调度复盘 A（UTC）", "不是时间"),
              doc("总调度复盘 B（UTC）", "2026-10-14T10:18:56Z"),
          ])["title"],
          "总调度复盘 B（UTC）")

    for f in failures:
        print("FAIL", f)
    print("self-test:", "ok" if not failures else f"{len(failures)} failed")
    return 1 if failures else 0


def main():
    if sys.argv[1:2] == ["--self-test"]:
        return self_test()
    raise SystemExit("orchestrator_actions.py 只提供函数；自测用 --self-test")


if __name__ == "__main__":
    sys.exit(main())
