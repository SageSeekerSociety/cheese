"""Whose part of a session's record is, and what anyone else reads in its place.

A session in a shared room works for one person a turn and reads that person's
private memory: its index is in the input the turn opens with, and its files
come back in the results of the session's own file tools. Everyone in the room
reads the session's records — the 现场, the raw records, their transcript
rows, each record on its own — and a private memory is only its owner's
(`routes/memory_files._readable`). `service.read` is what every one of those
reads goes through, and it hands each reader the records `for_reader` leaves.

Two marks say what is private, each put down by the code that knows it:

- An index in an input's text is enclosed by `private_span`: an opening tag that
  names its owner and a seal, and a closing tag that carries the same seal. The
  seal is a digest of the owner and the text, so the text cannot hold its own
  closing tag, and a copy cut short before its closing tag hides everything
  after the opening one.
- A file tool's call on a private memory path, and the result that answers it,
  carry the runner's stamp ``cheese.private`` (call id → owner), put on both
  records by the runner that saw the call (`claude_code.runner`).

This package is the bottom layer: the memory package that writes the index and
the runner that stamps the calls import it, never the other way round.
"""

import hashlib
import re
from typing import Any

_OPENING = re.compile(r'<private-memory owner="([^"]*)" seal="([0-9a-f]{32})">')
_MARK = "<private-memory"
# copy: needs product sign-off — what a reader sees in place of another
# person's private memory, an index or a file alike.
HIDDEN = "（{owner} 的 private 记忆，只有本人看得到）"


def _seal(owner: str, text: str) -> str:
    return hashlib.sha256(f"{owner}\0{text}".encode()).hexdigest()[:32]


def _closing(seal: str) -> str:
    return f'</private-memory seal="{seal}">'


def private_span(owner: str, text: str) -> str:
    """``text`` enclosed as ``owner``'s alone (see the module's opening)."""
    seal = _seal(owner, text)
    return f'<private-memory owner="{owner}" seal="{seal}">\n{text}\n{_closing(seal)}'


def _text_for(text: str, reader: str | None) -> str:
    """``text`` with every span that is not ``reader``'s replaced by `HIDDEN`."""
    if _MARK not in text:
        return text
    out: list[str] = []
    at = 0
    while found := _OPENING.search(text, at):
        owner, seal = found.groups()
        end = text.find(_closing(seal), found.end())
        stop = len(text) if end < 0 else end + len(_closing(seal))
        out.append(text[at : found.start()])
        out.append(
            text[found.start() : stop]
            if owner == reader
            else HIDDEN.format(owner=owner)
        )
        at = stop
    out.append(text[at:])
    return "".join(out)


def _holds_mark(value: Any) -> bool:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if _MARK in item:
                return True
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return False


def _spans_for(value: Any, reader: str | None) -> Any:
    """``value`` with `_text_for` applied to every string, copied where it
    changes and shared where it does not.

    Walked with a stack rather than by recursion: a record is whatever the
    harness wrote and can be nested deeper than the interpreter recurses. The
    records handed in are the stored ones, so nothing is changed in place, and
    a record with no span in it (nearly all) is handed back as it is."""
    if not _holds_mark(value):
        return value
    root = [value]
    pending: list[tuple[list | dict, Any]] = [(root, 0)]
    while pending:
        holder, at = pending.pop()
        item = holder[at]
        if isinstance(item, str):
            if _MARK in item:
                holder[at] = _text_for(item, reader)
        elif isinstance(item, list):
            copy = list(item)
            holder[at] = copy
            pending.extend((copy, n) for n in range(len(copy)))
        elif isinstance(item, dict):
            copy = dict(item)
            holder[at] = copy
            pending.extend((copy, key) for key in copy)
    return root[0]


def _calls_for(record: dict, reader: str | None) -> dict:
    """``record`` with the calls and results stamped as someone else's
    (``cheese.private``) emptied: a call keeps its tool's name, a result its
    call's id, and both say whose they were (`HIDDEN`)."""
    stamp = (record.get("cheese") or {}).get("private")
    if not isinstance(stamp, dict):
        return record
    others = {str(call): str(owner) for call, owner in stamp.items() if owner != reader}
    if not others:
        return record
    nested = record.get("type") == "cheese_file" and isinstance(
        record.get("entry"), dict
    )
    message = record["entry"] if nested else record
    body = message.get("message")
    if not isinstance(body, dict) or not isinstance(body.get("content"), list):
        return record
    blocks = body["content"]
    shown: list = []
    answered = False
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            owner = others.get(str(block.get("id")))
            if owner is not None:
                block = {**block, "input": {"file_path": HIDDEN.format(owner=owner)}}
        elif isinstance(block, dict) and block.get("type") == "tool_result":
            owner = others.get(str(block.get("tool_use_id")))
            if owner is not None:
                block = {**block, "content": HIDDEN.format(owner=owner)}
                answered = True
        shown.append(block)
    message = {**message, "message": {**body, "content": shown}}
    if answered:
        # Claude Code repeats a tool's result beside the message, whole.
        message.pop("tool_use_result", None)
    return {**record, "entry": message} if nested else message


def for_reader(record: dict, reader: str | None) -> dict:
    """``record`` as ``reader`` (a handle) may read it: nobody else's private
    memory in it. ``None`` is the platform reading for itself, never for a
    person, and gets the record as stored."""
    if reader is None:
        return record
    return _spans_for(_calls_for(record, reader), reader)
