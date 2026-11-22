"""Whose part of a session's record is, and what anyone else reads in its place.

A session in a shared room works for one person a turn and reads that person's
private memory with its own file tools. Everyone in the room reads the
session's records — the 现场, the raw records, their transcript rows, each
record on its own — and a private memory is only its owner's
(`routes/memory_files._readable`). `service.read` is what every one of those
reads goes through, and it hands each reader the records `for_reader` leaves.

What is private is decided only from what the platform itself recorded, never
from the text of a record: anyone can write text (a shared memory, a message,
a file in the project), so text that claims to be somebody's proves nothing.
The runner that saw a file tool's call on a private memory path stamps that
call, and the result that answers it, with the owner (``cheese.private``: call
id → owner; `claude_code.runner`). The same runner shows the room a call while
it is being written, before any record of it exists, and puts `hidden_input`
in place of such a call's arguments there. A person's private index is not in
any input the session is given (`prompts.memory.private_index_pointer`):
the session reads it, and that read is stamped like any other.

In a multi-person place the platform does put one class of a person's private
memory into an input: their `communication` notes (`prompts.talk_notes`). It
hands them to the runner apart from the rest of the input
(`session_host.contract.PrivateText`), the runner writes each as a text block
of its own and stamps every record that carries it with the block's index and
owner (``cheese.private_text``), and anyone else reads `HIDDEN` in that
block's place; the rest of the turn's input reads as it was.

This package is the bottom layer: the runner that stamps the calls imports it,
never the other way round. The runner's archive carries this module
(`claude_code.bundle`), so it is stdlib only.
"""

# copy: needs product sign-off — what a reader sees in place of another
# person's private memory.
HIDDEN = "（{owner} 的 private 记忆，只有本人看得到）"


def hidden_input(owner: str) -> dict:
    """What anyone but ``owner`` reads as the input of a file tool's call on
    ``owner``'s private memory: the stored call (`for_reader`) and the one the
    room watches being written (`claude_code.runner`) alike."""
    return {"file_path": HIDDEN.format(owner=owner)}


def for_reader(record: dict, reader: str | None) -> dict:
    """``record`` as ``reader`` (a handle) may read it: the calls and results
    stamped as someone else's (``cheese.private``) emptied — a call keeps its
    tool's name, a result its call's id, and both say whose they were — and
    so are the input blocks stamped as someone else's (``cheese.private_text``).
    ``None`` is the platform reading for itself, never for a person, and gets
    the record as stored. The record handed in is the stored one, so what
    changes is copied."""
    if reader is None:
        return record
    stamps = record.get("cheese") or {}
    stamp = stamps.get("private")
    texts = stamps.get("private_text")
    others = {
        str(call): str(owner)
        for call, owner in (stamp.items() if isinstance(stamp, dict) else ())
        if owner != reader
    }
    blocks = {
        str(index): str(owner)
        for index, owner in (texts.items() if isinstance(texts, dict) else ())
        if owner != reader
    }
    if not others and not blocks:
        return record
    nested = record.get("type") == "cheese_file" and isinstance(
        record.get("entry"), dict
    )
    message = record["entry"] if nested else record
    body = message.get("message")
    if not isinstance(body, dict) or not isinstance(body.get("content"), list):
        return record
    shown: list = []
    answered = False
    for index, block in enumerate(body["content"]):
        if (owner := blocks.get(str(index))) is not None and isinstance(block, dict):
            block = {**block, "text": HIDDEN.format(owner=owner)}
        elif isinstance(block, dict) and block.get("type") == "tool_use":
            owner = others.get(str(block.get("id")))
            if owner is not None:
                block = {**block, "input": hidden_input(owner)}
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
