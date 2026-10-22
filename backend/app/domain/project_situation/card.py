"""把模型的回答核对成「现状」卡：每一行留下的出处都是真的，状态由记录定。

模型只做两件事：把记录分成几块，给每块写名字和一句话。它说的出处（D1、T1，或项目
目标里的一句原话）在这里逐条对回材料：对不上的出处扔掉，一条出处都不剩的那一行整行
不要。每块的状态不听模型的，看它引了什么：

- 引了还开着的任务：进行中；
- 没有开着的任务，引了已采纳的交付：可用；
- 只引了项目目标里的一句：项目要做，但还没有任何任务，未开始。
"""

import enum
from typing import Any

from app.domain.project_situation.records import Delivery, OpenTask, Records

#: 最多几块：再多就不是一眼看清了。
MAX_PARTS = 5


class Status(enum.StrEnum):
    usable = "usable"
    underway = "underway"
    not_started = "not_started"


def _squash(text: str) -> str:
    return "".join(text.split())


def _source(record: Delivery | OpenTask) -> dict[str, Any]:
    if isinstance(record, Delivery):
        return {
            "kind": "delivery",
            "card_id": str(record.card_id),
            "task_id": str(record.task_id),
            "room_id": str(record.room_id),
            "title": record.subject or record.task_title,
        }
    return {
        "kind": "task",
        "task_id": str(record.task_id),
        "room_id": str(record.room_id),
        "title": record.title,
    }


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def build(answer: object, records: Records) -> list[dict[str, Any]]:
    """核对过的那几块；模型的回答对不上任何记录时是空的。"""
    parts = answer.get("parts") if isinstance(answer, dict) else None
    if not isinstance(parts, list):
        return []
    known = records.by_ref()
    goal = _squash(records.goal)
    built: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        name, text = _text(part.get("name")), _text(part.get("text"))
        if not name or not text:
            continue
        refs = part.get("sources")
        listed = refs if isinstance(refs, list) else []
        cited = [
            known[ref]
            for ref in dict.fromkeys(r for r in listed if isinstance(r, str))
            if ref in known
        ]
        quote = _text(part.get("goal"))
        quoted = bool(quote) and _squash(quote) in goal
        if not cited and not quoted:
            continue
        if any(isinstance(r, OpenTask) for r in cited):
            status = Status.underway
        elif cited:
            status = Status.usable
        else:
            status = Status.not_started
        sources = [_source(r) for r in cited]
        if quoted:
            sources.append({"kind": "goal", "quote": quote})
        built.append(
            {"name": name, "text": text, "status": status.value, "sources": sources}
        )
        if len(built) == MAX_PARTS:
            break
    return built
