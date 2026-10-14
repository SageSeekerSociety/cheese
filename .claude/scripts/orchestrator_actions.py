"""Extract action items from a retrospective's Markdown, without guessing fields."""

import re


def action_items(markdown):
    """Prefer 行动项; accept 改进 in older retrospectives. Preserve source order."""
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
            if title in ("行动项", "改进"):
                active = []
                level = depth
                sections.append((title, active))
            continue
        if active is not None:
            active.append(line)
    preferred = [lines for title, lines in sections if title == "行动项"]
    chosen = preferred or [lines for title, lines in sections if title == "改进"]
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


def action_table(markdown):
    """List the source items; absent owner/layer/check stay explicit as —."""
    items = action_items(markdown)
    rows = ["## 行动项核对表", "", "| 行动项 | owner | 层 | 怎么核 |",
            "| --- | --- | --- | --- |"]
    for item in items:
        fields = {}
        for match in re.finditer(
            r"(?:^|[；;])\s*(owner|负责人|层|怎么核)\s*[:：]\s*([^；;]+)", item
        ):
            key = "owner" if match[1] == "负责人" else match[1]
            fields[key] = match[2].strip()
        cells = [item, fields.get("owner", "—"), fields.get("层", "—"),
                 fields.get("怎么核", "—")]
        rows.append("| " + " | ".join(cell.replace("|", r"\|") for cell in cells) + " |")
    rows.extend(["", f"行动项：{len(items)} 条"])
    return "\n".join(rows)
