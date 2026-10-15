"""A canvas as text, the way an AI teammate reads it.

A board is drawn for people; a model reads it as an outline. Each card is a
bullet that starts with its number (``[3]``); the cards a card's lines lead to
are indented under it, each line written ``—words→`` (``→`` alone when the line
carries none). A card reached a second time is named, not repeated. Cards with
no lines are listed last. A card that points at a task says which task and
where it stands; one a person changed last says who and when, so the reader
knows to think twice before undoing it — nothing stops it from changing the
card, it is told so that it can judge.

Pure: what the cards, lines and tasks are comes in; the same board always reads
the same, so a prompt that carries it is stable from turn to turn.
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class TaskLine:
    """What a card says about the task it points at."""

    number: int | None
    title: str
    #: Where it stands: 进行中, 已结束.
    state: str


@dataclass(frozen=True, slots=True)
class CardText:
    id: uuid.UUID
    number: int
    content: str
    task_id: uuid.UUID | None
    updated_by: str
    updated_at: datetime
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None


@dataclass(frozen=True, slots=True)
class EdgeText:
    source_id: uuid.UUID
    target_id: uuid.UUID
    label: str


def _card_head(
    card: CardText,
    tasks: Mapping[uuid.UUID, TaskLine],
    people: Iterable[str],
) -> list[str]:
    lines = card.content.strip().splitlines() or ["（空）"]
    head = f"[{card.number}] {lines[0].strip()}"
    notes: list[str] = []
    if card.task_id is not None:
        task = tasks.get(card.task_id)
        if task is None:
            notes.append("关联的任务你看不到")
        else:
            name = f"#{task.number}" if task.number is not None else ""
            notes.append(f"任务{name}《{task.title}》{task.state}")
    if card.updated_by in people:
        when = card.updated_at.strftime("%m-%d %H:%M")
        notes.append(f"@{card.updated_by} 最后改过，{when} UTC")
    if notes:
        head += "〔" + "；".join(notes) + "〕"
    return [head, *lines[1:]]


def outline(
    cards: Sequence[CardText],
    edges: Sequence[EdgeText],
    *,
    tasks: Mapping[uuid.UUID, TaskLine],
    people: Iterable[str],
    title: str = "",
    canvas_id: uuid.UUID | None = None,
    positions: bool = False,
) -> str:
    """The board as an indented outline. ``people`` are the handles of
    persons, not AI teammates: a card one of them changed last says so."""
    people = set(people)
    by_id = {card.id: card for card in cards}
    ordered = sorted(cards, key=lambda card: card.number)
    out_edges: dict[uuid.UUID, list[EdgeText]] = {}
    has_in: set[uuid.UUID] = set()
    for edge in edges:
        if edge.source_id in by_id and edge.target_id in by_id:
            out_edges.setdefault(edge.source_id, []).append(edge)
            has_in.add(edge.target_id)
    for leaving in out_edges.values():
        leaving.sort(key=lambda edge: by_id[edge.target_id].number)

    name = f"《{title}》" if title else ""
    head = f"画布{name}"
    if canvas_id is not None:
        head += f"（canvas: {canvas_id}）"
    head += f"：{len(cards)} 个格子，{len(edges)} 条连线。"
    if not cards:
        return head + "还没有格子。"

    body: list[str] = []
    shown: set[uuid.UUID] = set()

    def walk(card: CardText, depth: int, via: EdgeText | None) -> None:
        pad = "  " * depth
        arrow = ""
        if via is not None:
            arrow = f"—{via.label}→ " if via.label else "→ "
        if card.id in shown:
            body.append(f"{pad}- {arrow}[{card.number}]（见上）")
            return
        shown.add(card.id)
        first, *rest = _card_head(card, tasks, people)
        body.append(f"{pad}- {arrow}{first}")
        body.extend(f"{pad}  {line}" if line.strip() else "" for line in rest)
        for edge in out_edges.get(card.id, []):
            walk(by_id[edge.target_id], depth + 1, edge)

    connected = [card for card in ordered if card.id in out_edges or card.id in has_in]
    for card in connected:
        if card.id not in has_in and card.id not in shown:
            walk(card, 0, None)
    # What is left of the connected cards sits on a loop: start at its lowest number.
    for card in connected:
        if card.id not in shown:
            walk(card, 0, None)
    alone = [card for card in ordered if card.id not in shown]
    if alone:
        if body:
            body.append("")
            body.append("没有连线的格子：")
        for card in alone:
            walk(card, 0, None)

    text = head + "\n\n" + "\n".join(body)
    if positions:
        rows = [
            f"[{card.number}] "
            + (
                "未摆放"
                if card.x is None or card.y is None
                else f"x={round(card.x)} y={round(card.y)}"
            )
            + (
                f" 宽 {round(card.width)} 高 {round(card.height)}"
                if card.width and card.height
                else ""
            )
            for card in ordered
        ]
        text += "\n\n位置（左上角，单位像素）：\n" + "\n".join(rows)
    return text


def clipped(text: str, budget: int, *, hint: str) -> str:
    """``text`` cut to ``budget`` characters at a line, saying where the rest is."""
    if len(text) <= budget:
        return text
    cut = text.rfind("\n", 0, budget)
    kept = text[: cut if cut > 0 else budget]
    return f"{kept}\n……（画布太大，只列了前 {len(kept)} 字；{hint}）"
