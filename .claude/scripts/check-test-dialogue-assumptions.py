#!/usr/bin/env python3
"""check-test-dialogue-assumptions.py — a test may not pin a conversation's
shape without saying which blocks it means.

WHY THIS EXISTS. A conversation is not only what the test put in it. Between
the test's own seeds the platform writes lines of its own: a task that was made
already started says so, a routine fires, a poll notices a red check, a card is
adopted. #3489 (a task starts as it is created, 2026-10-12) added exactly one
such line, and the tests that broke were not the ones that asked about events —
they were the two that read a conversation as a whole:

    assert [b["id"] for b in _history(client, task, q="Task")["data"]] == ids[1:]
    assert _events(client, rid) == []

Neither says "the message I just sent"; both say "this conversation holds
exactly this and nothing else", which stops being true the moment the platform
says one more thing. The failure is a bare `assert [...] == [...]` that names
no change the author made, and three CI rounds went into reading it.

WHAT IT REJECTS. In `backend/tests/`, a read of one conversation — the
conversation-message endpoint `/topics/<id>/history`, the 现场 read
`/topics/<id>/transcript`, or a `Block` query pinned to one `conversation_id` —
whose result is then used as if the conversation held nothing else:

  * `index`  — read by position: `blocks[0]`, `blocks[-1]`, `...["data"][0]`;
  * `length` — pinned to an exact size: `len(blocks) == 2`, `len(blocks) != 1`;
  * `whole`  — compared as a whole against something that is not itself a read
    of the same conversation: `blocks == []`, `[b.id for b in blocks] == ids`.

The three are one assumption and three repairs, so they are three kinds in the
baseline, and a narrowed read is none of them. Narrowing is by `author` / `meta`
/ `event_type` / `reply_to`, or by `kind` to something other than the event
slice — either at the call site (a keyword, or a key of a `params=` dict) or as
the filter the helper applies to the rows it returns. `q=` alone is not
narrowing: the 2026-10-12 fix added `kind="message"` to a call that already had
`q=`, and a platform line can carry any text a search looks for. Narrowing to
`BlockKind.event` is not narrowing either: that is the slice a new announcement
is written into, and it is what `assert _events(...) == []` assumed.

PAYING ONE DOWN. The repair is to say which blocks the assertion is about
(`kind="message"`), or to compare the conversation with itself across the
action — the ids before it ran against the ids after (`said` in the fixed
`test_task_naming.py`). Both are reads of the same conversation, so neither is
a `whole`.

WHAT IS NOT REJECTED. `<` and `>` against a length: `while len(blocks) < 2`
waits for something to arrive, which is the opposite of assuming nothing else
will. Windows (`before=`, `after=`, `has_more`, `oldest_id`): the response
hands those to the caller, who is not guessing at a position. A read whose
whole result is compared with another read of the same conversation.

GRANDFATHERING. The shapes already in the tree are frozen in `BASELINE`
exactly: one entry per (file, kind, expression), the expression being its
normalized source. The set may only shrink — a frozen entry nothing matches any
more is a failure, not a pass, so paying one site down does not buy a new
violation somewhere else. A baseline that has drifted out of sight reads
exactly like a clean tree, which is the one failure mode a guard cannot afford.

Usage: check-test-dialogue-assumptions.py [--root DIR] [--json] [--dump]
Exit 0 the tree matches the frozen baseline, 1 it does not, 2 the tree could
not be judged (a test file that will not parse).
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

#: The three shapes and the sentence that repairs each. Kept apart because each
#: fails for the same reason but is edited somewhere else.
HINTS = {
    "index": (
        "按位置取块，却没有先说清要的是哪一类：平台自己补一句公告，这个下标就挪了。"
        "先收窄再取位置（params={\"kind\": \"message\"}／kind=\"message\"），"
        "或者改成比较这次动作前后的同一类块。"
    ),
    "length": (
        "钉死了这段对话里有几个块，却没有先说清数的是哪一类：平台再补一句公告，"
        "这个数字就变了。先收窄到 kind/author，或者改成「这次动作没多出东西」"
        "（跑前跑后的块 id 相同）。"
    ),
    "whole": (
        "拿整段对话跟一个固定的东西比：这段对话里还有平台自己的话，它多一句就红。"
        "把读取收窄到你要的那一类（kind=\"message\"），或者拿这段对话跟它自己比"
        "（这次动作跑之前的块 id 对跑之后的）。"
    ),
}

#: Keys that narrow a read down to the blocks the test means. `q` is deliberately
#: absent: #3489's fix added `kind="message"` to a call that already had `q=`.
NARROWING = frozenset({"kind", "kinds", "author", "meta", "event_type", "reply_to"})

#: Narrowing to the platform's own lines is not narrowing. #3489's other red
#: test was `assert _events(client, rid) == []`: filtered down to the event
#: kind, which is exactly the slice a new announcement lands in.
EVENT_MARKERS = ("BlockKind.event", '"event"', "'event'")

#: The keys that say which blocks a read means, read off the column a filter
#: compares rather than off the text of the whole filter — in
#: `Block.conversation_id == topic.id` the `.id` belongs to the argument.
#:
#: `id` and `content` are here for the same reason as `kind`: `[b for b in
#: blocks if b["id"] == said]` has said which block it means, and one more
#: platform line cannot join that slice.
ROW_KEYS = frozenset({
    "kind", "kinds", "author", "meta", "event_type", "event_types",
    "reply_to", "id", "content", "turn_id", "reactions", "run_id", "tasks",
})

#: A conversation's blocks come from `/history`, `/transcript` or `/blocks` under
#: `/topics/`. All three hand back the topic's timeline. The app's other
#: `/history` endpoints are a team's credit log or a document's versions, which
#: no announcement joins.
TOPIC_SEGMENTS = ("/history", "/transcript", "/blocks")

#: The columns that say which conversation a block belongs to. A `Block` query
#: that names any of them is a read of one conversation, wherever it is written.
CONVERSATION_COLUMNS = ("conversation_id", "topic_id", "room_id")

#: The patterns already in the tree, frozen. One entry per finding: the file
#: under `backend/tests/`, which of the three shapes, and the expression's
#: normalized source. Delete an entry only by fixing the test it names.
_INTEGRATION = "backend/tests/integration/"
_UNIT = "backend/tests/unit/"


def _FROZEN(
    path: str, kind: str, source: str, times: int = 1
) -> tuple[tuple[str, str, str], ...]:
    """The same frozen finding `times` times.

    The baseline is a multiset, not a set: a second copy of an expression a file
    already has is a second violation, and copy-paste is how this kind of
    assertion spreads. Writing the count here keeps it visible in the literal.
    """
    return ((path, kind, source),) * times

#: One entry per finding, grouped by file; the file sits in `_INTEGRATION` or
#: `_UNIT` and the rest is its name. The expression is `ast.unparse` with runs of
#: whitespace collapsed, so reindenting a statement does not make an entry read
#: as stale. Renaming a local variable does: its name is part of the source.
#: That is deliberate — the entry is meant to be deleted only by fixing the
#: test it names, and a rename is an edit somebody should look at.
BASELINE: tuple[tuple[str, str, str], ...] = (
    # A thread's conversation: each of these says the thread holds exactly the
    # blocks the test posted into it.
    (_INTEGRATION + "test_a_thread_reads_the_message_it_hangs_under.py", "whole", "[b['content'] for b in page['data']] == ['微信里打不开预览', 'uploads/spec.pdf']"),
    (_INTEGRATION + "test_a_thread_reads_the_message_it_hangs_under.py", "whole", "[b['id'] for b in found['data']] == [root['id']]"),
    (_INTEGRATION + "test_a_thread_reads_the_message_it_hangs_under.py", "whole", "[b['id'] for b in newest['data']] == [b['id'] for b in said[1:]]"),
    (_INTEGRATION + "test_a_thread_reads_the_message_it_hangs_under.py", "whole", "[b['id'] for b in older['data']] == [root['id'], said[0]['id']]"),
    (_INTEGRATION + "test_a_thread_reads_the_message_it_hangs_under.py", "whole", "[b['id'] for b in page['data']] == [root['id'], said['id']]"),
    *_FROZEN(_INTEGRATION + "test_a_thread_reads_the_message_it_hangs_under.py", "whole", "[b['id'] for b in page['data']] == [root['id']]", 2),
    (_INTEGRATION + "test_a_thread_reads_the_message_it_hangs_under.py", "whole", "older_still['data'] == []"),
    # A room's history: pages, slices, and the one search by a payload.
    (_INTEGRATION + "test_chat_history.py", "index", "_history(client, room, q='配置文件缺失')['data'][0]"),
    (_INTEGRATION + "test_chat_history.py", "whole", "[b['id'] for b in data['data']] == ids[:1]"),
    (_INTEGRATION + "test_chat_history.py", "whole", "[b['id'] for b in forward['data']] == ids[1:4]"),
    (_INTEGRATION + "test_chat_history.py", "whole", "[b['id'] for b in latest['data']] == ids[-50:]"),
    (_INTEGRATION + "test_chat_history.py", "whole", "[b['id'] for b in next_page['data']] == ids[4:7]"),
    (_INTEGRATION + "test_chat_history.py", "whole", "[b['id'] for b in older['data']] == ids[:7]"),
    # A transcript window: the whole read is what the two windows add up to,
    # which is what that test is about.
    (_INTEGRATION + "test_tool_event_meta.py", "index", "client.get(f\"/topics/{a['id']}/transcript\").json()['data']['data'][0]"),
    (_INTEGRATION + "test_tool_event_meta.py", "index", "everything[-1]"),
    (_INTEGRATION + "test_tool_event_meta.py", "index", "first['data'][-1]"),
    (_INTEGRATION + "test_tool_event_meta.py", "length", "first['data']"),
    # Said-with-position rather than said-with-a-filter, in the rest of the tree.
    (_INTEGRATION + "test_a_routine_run_is_a_message.py", "index", "main[-1]"),
    (_INTEGRATION + "test_block_paging.py", "index", "_blocks(client, tid, shown='false')['data'][10]"),
    *_FROZEN(_INTEGRATION + "test_block_paging.py", "index", "payload['data'][-1]", 2),
    *_FROZEN(_INTEGRATION + "test_block_paging.py", "index", "payload['data'][0]", 2),
    (_INTEGRATION + "test_block_paging.py", "length", "payload['data']"),
    (_INTEGRATION + "test_block_paging.py", "whole", "payload['data'] == []"),
    (_INTEGRATION + "test_chat_flow.py", "index", "blocks[0]"),
    *_FROZEN(_INTEGRATION + "test_chat_flow.py", "index", "blocks[1]", 2),
    (_UNIT + "test_runtime.py", "index", "rows[0]"),
    (_UNIT + "test_runtime.py", "length", "rows"),
    (_UNIT + "test_runtime.py", "whole", "events == chat.events == rows == []"),
    # Compared as a whole against a list the test wrote out.
    (_INTEGRATION + "test_attachments.py", "whole", "[b['kind'] for b in main] == ['message', 'attachment']"),
    (_INTEGRATION + "test_block_paging.py", "whole", "[b['id'] for b in steps] == [b['id'] for b in hidden]"),
    (_INTEGRATION + "test_chat_flow.py", "whole", "[b['author'] for b in blocks] == ['user-1', agent]"),
    (_INTEGRATION + "test_chat_flow.py", "whole", "[b['author'] for b in blocks] == ['user-1']"),
    (_INTEGRATION + "test_event_author.py", "whole", "kinds == ['message', 'attachment', 'message']"),
    (_INTEGRATION + "test_room_subscription_tail.py", "whole", "[b['id'] for b in missed] == [between['id']]"),
    (_INTEGRATION + "test_room_subscription_tail.py", "whole", "[b['id'] for b in missed] == [late_id]"),
    (_INTEGRATION + "test_threads.py", "whole", "[b['id'] for b in main] == [asked['id']]"),
    # "This action left nothing behind", said against the whole timeline.
    *_FROZEN(_INTEGRATION + "test_chat_ws_auth.py", "whole", "_blocks(client, tid) == []", 7),
)


# --------------------------------------------------------------------------- #
# Reading the tree
# --------------------------------------------------------------------------- #


def _path_text(node: ast.AST) -> str | None:
    """The literal request path of a call argument, `\0` where a value is spliced."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            piece.value if isinstance(piece, ast.Constant) and isinstance(piece.value, str) else "\0"
            for piece in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _path_text(node.left), _path_text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


#: Calls that put a URL on the wire. A function that merely TAKES a path string
#: (`room_from_path("/topics/x/blocks")`) is not reading anything.
HTTP_CALLS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "request", "stream"})

#: ORM calls that hand back the rows a query selects. The query is the argument.
SESSION_CALLS = frozenset({"scalars", "execute", "stream"})


def _reads_topic_conversation(node: ast.Call) -> bool:
    """`client.get(f"/topics/{topic}/history", …)` and the 现场 read beside it.

    The path has to END at the collection: `/history/{block_id}` reads one block
    the caller named by id, and a single block is not a conversation nobody
    else writes to. A query string comes off first — `?q=Task` is a filter on
    the read, not a different endpoint.
    """
    if not isinstance(node.func, ast.Attribute) or node.func.attr not in HTTP_CALLS:
        return False
    arguments = [*node.args, *(kw.value for kw in node.keywords)]
    for arg in arguments:
        text = _path_text(arg)
        if not text:
            continue
        path = text.split("?", 1)[0].split("#", 1)[0]
        if "/topics" in path and any(path.endswith(seg) for seg in TOPIC_SEGMENTS):
            return True
    return False


def _query_root(node: ast.Call) -> ast.Call | None:
    """The `select(...)` a call chain was built from, if it was built from one.

    `select(Block).where(…)` is one call wrapped in another; `len(…)` and
    `"\\n".join(…)` are calls that merely have a query somewhere inside them.
    Judging the outermost call would make those readers of the conversation
    too, so the chain is followed down to its root instead.
    """
    current: ast.AST = node
    while isinstance(current, ast.Call):
        func = current.func
        if isinstance(func, ast.Name):
            return current if func.id == "select" else None
        if not isinstance(func, ast.Attribute):
            return None
        # `sqlalchemy.select(...)` and `sa.select(...)` are the same query as
        # the bare import; only the name it is reached through differs.
        if func.attr == "select":
            return current
        current = func.value
    return None


def _reads_blocks_of_one_conversation(node: ast.Call) -> bool:
    """A `Block` query pinned to one conversation: the DB side of the same read.

    Pinned by any of the columns that name a conversation, spelled
    `Block.topic_id == topic`, `Block.topic_id.in_([topic])` or with the column
    on the left — the tests use all three. Only a chain rooted at `select(Block)`
    qualifies, and only the part of it after `Block` is read, so `select(Block)`
    alone stays "every block in the database".

    A column of `Block` counts too: `select(Block.id).where(…)` is the same
    conversation read down to its ids, which is how a test snapshots the blocks
    before an action and compares them with the blocks after it.
    """
    root = _query_root(node)
    if root is None or not root.args:
        return False
    first = root.args[0]
    if isinstance(first, ast.Name):
        selected = first.id
    elif isinstance(first, ast.Attribute) and isinstance(first.value, ast.Name):
        selected = first.value.id
    else:
        selected = None
    if selected != "Block":
        return False
    source = ast.unparse(node)
    marker = "select(Block"
    tail = source[source.find(marker) + len(marker):]
    return any(
        marker in tail
        for column in CONVERSATION_COLUMNS
        for marker in (f"{column} ==", f"{column}.in_(")
    )


def _callee(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr if not isinstance(func.value, ast.Name) else f"{func.value.id}.{func.attr}"
    return None


def _narrows_to_events(expression: str) -> bool:
    return any(marker in expression for marker in EVENT_MARKERS)


def _call_narrows(node: ast.Call) -> bool:
    """Narrowing asked for at the call: a keyword, or a key of a `params=` dict.

    Any narrowing that is not to the event slice is enough to make the read the
    test's own — `kind="message"` beside `kind="event"` still excludes the
    platform's lines.
    """
    for kw in node.keywords:
        if kw.arg in NARROWING and not _narrows_to_events(ast.unparse(kw.value)):
            return True
        if kw.arg == "params" and isinstance(kw.value, ast.Dict):
            for key, value in zip(kw.value.keys, kw.value.values):
                if not (isinstance(key, ast.Constant) and key.value in NARROWING):
                    continue
                if not _narrows_to_events(ast.unparse(value)):
                    return True
    return False


def _url_narrows(node: ast.Call) -> bool:
    """Narrowing written into the path itself: `/history?kind=message`.

    `_reads_topic_conversation` takes the query string off to find the endpoint,
    so a narrowed read spelled this way has said which blocks it means just as
    clearly as `params={"kind": "message"}` has.
    """
    for arg in (*node.args, *(kw.value for kw in node.keywords)):
        text = _path_text(arg)
        if not text or "?" not in text:
            continue
        query = text.split("?", 1)[1].split("#", 1)[0]
        keys = {piece.split("=", 1)[0] for piece in query.split("&") if piece}
        if keys & NARROWING and not _narrows_to_events(query):
            return True
    return False


def _narrowing_column(node: ast.AST) -> bool:
    """The column a filter compares: `Block.kind`, `b["author"]`, `b.tasks`.

    Followed through the accessors SQLAlchemy puts on a JSON column, so
    `Block.meta["event_type"].as_string()` reads as the `event_type` key it is,
    and not as a filter on something unnamed.
    """
    if isinstance(node, ast.Attribute):
        return node.attr in ROW_KEYS or _narrowing_column(node.value)
    if isinstance(node, ast.Subscript):
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return key.value in ROW_KEYS
        return _narrowing_column(node.value)
    if isinstance(node, ast.Call):
        return _narrowing_column(node.func)
    return False


def _query_narrows(node: ast.Call) -> bool:
    """A `select(Block)` chain whose `.where(…)` says which blocks it means.

    Only the filter arguments are read, not the select list: `.where(…)` on
    `select(Block.id)` narrows nothing, and `.id` in the select list used to
    read as a filter and retire the whole query.

    Which column a comparison is ABOUT is read from the expression, not from
    the text: the left side is the column (`Block.kind == "message"`), while a
    key on the right is an argument (`Block.conversation_id == topic.id`).
    Matching `.id` anywhere in the filter would read that pin as narrowing.
    """
    filters: list[ast.AST] = []
    keywords: list[ast.keyword] = []
    current: ast.AST = node
    while isinstance(current, ast.Call):
        func = current.func
        if not isinstance(func, ast.Attribute):
            break
        if func.attr in ("where", "filter_by", "filter"):
            filters.extend(current.args)
            keywords.extend(
                keyword
                for keyword in current.keywords
                if keyword.arg is not None
            )
        current = func.value
    for keyword in keywords:
        # `filter_by(kind="event")` names the same slice as `.where(…)` does,
        # and the event slice is not narrowing: announcements land there.
        if keyword.arg in NARROWING and not _narrows_to_events(
            ast.unparse(keyword.value)
        ):
            return True
    for argument in filters:
        for inner in ast.walk(argument):
            if isinstance(inner, ast.Compare) and _narrowing_column_on_either_side(
                inner
            ):
                if not _narrows_to_events(ast.unparse(inner)):
                    return True
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr in _FILTER_METHODS
                and _narrowing_column(inner.func.value)
                and not _narrows_to_events(ast.unparse(inner))
            ):
                return True
    return False


#: Methods SQLAlchemy turns into a `WHERE` clause. Matched on the column, so
#: `Block.content.startswith(…)` narrows by content while `topic.id.in_(…)`
#: (the conversation pin itself) stays a pin.
_FILTER_METHODS = frozenset(
    {
        "in_",
        "notin_",
        "is_",
        "is_not",
        "startswith",
        "endswith",
        "ilike",
        "like",
        "contains",
        "between",
    }
)


def _narrowing_column_on_either_side(node: ast.Compare) -> bool:
    """Whether this comparison filters on a row key.

    `Block.kind == "message"` and `"message" == Block.kind` are the same
    filter. A key on the RIGHT is usually an argument (`Block.conversation_id
    == topic.id`, where `topic.id` is not a filter at all), so only a literal
    on the left earns the reversed reading.
    """
    if _narrowing_column(node.left):
        return True
    return (
        isinstance(node.left, ast.Constant)
        and bool(node.comparators)
        and _narrowing_column(node.comparators[0])
    )


def _own_returns(func: ast.AST) -> list[ast.Return]:
    """The `return`s in a function's own body, not in a function defined in it."""
    return [
        node
        for node in _own_nodes(func)
        if isinstance(node, ast.Return) and node.value is not None
    ]


def _nested_functions(scope: ast.AST) -> dict[str, ast.AST]:
    """The functions defined in this scope's own body, by the name they bind."""
    body = getattr(scope, "body", None)
    if not isinstance(body, list):
        return {}
    return {
        node.name: node
        for node in body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class _Readers:
    """Which functions HAND BACK one conversation's blocks, unnarrowed.

    What a helper returns decides, not what it happens to read on the way past:
    `_reads` returns `.status_code`, `_meta` returns one block's `meta` and
    `_block_reactions` returns `reactions`; all three read `/topics/<id>/blocks`,
    and none of them hands back the timeline.

    Resolution is by SCOPE, not by bare name. Two helpers in one file may each
    define their own `async def read()`; the one that returns the blocks says
    nothing about the one that returns run records. A call is matched to the
    definition its scope binds, so `asyncio.run(read())` is judged against the
    `read` written beside it.
    """

    def __init__(self) -> None:
        self._seen: dict[int, bool] = {}
        #: Definitions being classified further up the stack right now. Called
        #: again from inside, the answer is provisional and never cached.
        self._evaluating: set[int] = set()
        #: Every definition met, with the scope it was judged in, so a later
        #: pass can ask again with the answers that have changed.
        self._defs: dict[int, tuple[ast.AST, dict[str, ast.AST]]] = {}

    def names(self, scope: dict[str, ast.AST]) -> frozenset[str]:
        """The names in this scope that are bound to a reader."""
        return frozenset(
            name
            for name, node in scope.items()
            if not name.startswith("test_") and self.is_reader(node, scope)
        )

    def is_reader(self, func: ast.AST, scope: dict[str, ast.AST]) -> bool:
        key = id(func)
        if key in self._seen:
            return self._seen[key]
        if key in self._evaluating:
            # A definition that reaches itself through a call is not a reader.
            # This answer is provisional: it is not cached, because it depends
            # on a classification that has not finished yet.
            return False
        self._defs.setdefault(key, (func, scope))
        self._evaluating.add(key)
        try:
            result = self._classify(func, scope)
        finally:
            self._evaluating.discard(key)
        self._seen[key] = result
        return result

    def _classify(self, func: ast.AST, scope: dict[str, ast.AST]) -> bool:
        local = {**scope, **_nested_functions(func)}
        readers = self.names(local)
        held = _held_names(func, readers)
        return any(
            self._returns(ret, held, readers, local) for ret in _own_returns(func)
        )

    def settle(self) -> None:
        """Re-ask every "no" until no answer turns into a "yes".

        "Is a reader" only ever turns from False to True, and it is only ever
        consulted positively: returning another helper's result is returning
        the conversation. So a single pass can leave a helper False purely
        because the helper it delegates to was still being classified at the
        time — which made the guard depend on the order the two are defined in.
        Asking the False answers again until none changes removes that: a
        delegating helper is a reader wherever it is written.
        """
        changed = True
        while changed:
            changed = False
            for key, (func, scope) in list(self._defs.items()):
                if self._seen.get(key):
                    continue
                self._seen.pop(key, None)
                if self.is_reader(func, scope):
                    changed = True

    def _returns(
        self,
        ret: ast.Return,
        held: set[str],
        readers: frozenset[str],
        local: dict[str, ast.AST],
    ) -> bool:
        value = ret.value
        if value is None:
            return False
        if _derives_from_conversation(value, held, readers):
            return True
        # `asyncio.run(read())`: the value comes from a reader defined inside
        # this very function, so the definition beside it is what decides.
        # Only the calls that BUILD the returned value are followed, and only
        # through arguments handed on unchanged. A reader called deeper inside
        # — in a comprehension's filter, say — was narrowed before it was
        # returned, which `_derives_from_conversation` has already judged.
        if isinstance(value, ast.Await):
            value = value.value
        if not isinstance(value, ast.Call):
            return False
        handed_on = [value, *(arg for arg in value.args if isinstance(arg, ast.Call))]
        for call in handed_on:
            if not isinstance(call.func, ast.Name):
                continue
            inner = local.get(call.func.id)
            if inner is not None and self.is_reader(inner, local):
                return True
        return False


# --------------------------------------------------------------------------- #
# Finding the three shapes
# --------------------------------------------------------------------------- #

#: Calls that hand a payload on unchanged. Only calls, never bare attributes:
#: `response.status_code` is a field of the response, not blocks, and following
#: it turns every `assert response.status_code == 200` into a false positive.
#: `set(rows)` and `sorted(rows)` hand the blocks on just as `list(rows)` does.
#: Builtins are listed apart because `all(rows)` is a bool, not the blocks: the
#: predicate reading them has already said which blocks it means.
BUILTIN_WRAPPERS = frozenset({"list", "set", "tuple", "sorted", "reversed"})
WRAPPER_CALLS = frozenset(
    {"json", "scalars", "all", "items", "copy", "list", "set", "tuple", "sorted"}
)

#: Where the response envelope keeps the blocks. A subscript with any other
#: string key reads a field beside them (`page["has_more"]`), which is not the
#: conversation and must not be judged as one.
ENVELOPE_KEYS = frozenset({"data", "items"})


def _position(key: ast.AST) -> bool:
    """A subscript that took one block out, rather than a field or a window.

    `blocks[0]` and `blocks[-1]` are positions. `blocks[:2]` is the window the
    caller asked for, and `page["data"]` is the envelope.

    A variable subscript is not guessed at either way: `blocks[i]` indexes a
    block, `page[field]` names a field beside them, and the two are the same
    expression to the parser. It is read as the collection, so
    `assert page[field] == []` is still caught as a whole conversation
    compared with emptiness, and `blocks[i]` alone is left alone.
    """
    if isinstance(key, ast.Slice):
        return False
    if isinstance(key, ast.Constant):
        return isinstance(key.value, int) and not isinstance(key.value, bool)
    if isinstance(key, ast.UnaryOp) and isinstance(key.op, ast.USub):
        return _position(key.operand)
    return False


def _derives_from_conversation(node: ast.AST, held: set[str], readers: set[str]) -> bool:
    """Is this expression a collection of one conversation's blocks?

    Widening rather than narrowing is what a guard must not do, so an
    unrecognised shape reads as "not blocks" and is left alone.
    """
    if isinstance(node, ast.Name):
        return node.id in held
    if isinstance(node, ast.Await):
        return _derives_from_conversation(node.value, held, readers)
    if isinstance(node, ast.Call):
        narrowed = _call_narrows(node) or _url_narrows(node)
        if _callee(node) in readers:
            return not narrowed
        if _reads_topic_conversation(node):
            return not narrowed
        if _reads_blocks_of_one_conversation(node):
            # The query's own `.where(…)` narrows it too, not only its keywords.
            return not narrowed and not _query_narrows(node)
        if isinstance(node.func, ast.Attribute) and node.func.attr in SESSION_CALLS:
            # `session.scalars(q)` hands back the rows the query selects.
            return any(
                _derives_from_conversation(arg, held, readers) for arg in node.args
            )
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in BUILTIN_WRAPPERS
            and node.args
        ):
            # The builtin `list(rows)`; `sorted(rows, key=…)` reorders the same
            # blocks and hands all of them on. The method spellings are below.
            return _derives_from_conversation(node.args[0], held, readers)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in WRAPPER_CALLS
            and not node.args
            and not node.keywords
        ):
            return _derives_from_conversation(node.func.value, held, readers)
        return False
    if isinstance(node, ast.Subscript):
        key = node.slice
        if _position(key):
            # A block, not the collection. That subscript is the finding itself.
            return False
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            # `page["has_more"]` is a field beside the blocks, not the blocks.
            return key.value in ENVELOPE_KEYS and _derives_from_conversation(
                node.value, held, readers
            )
        return _derives_from_conversation(node.value, held, readers)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        # `older["data"] + first["data"]`: two windows of the same conversation.
        return _derives_from_conversation(
            node.left, held, readers
        ) or _derives_from_conversation(node.right, held, readers)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        # `[b for b in blocks if b.meta["tool"] == "Task"]` is the narrowing
        # written as a filter instead of a query parameter. Any filter counts,
        # not only one over a row key: `[line for line in said if "任务已重新
        # 打开" in line]` has picked the message the test posted, and no
        # platform line joins it. What does NOT count is filtering down to the
        # event slice, which is where announcements land.
        filters = [ast.unparse(condition) for gen in node.generators for condition in gen.ifs]
        if filters and not all(_narrows_to_events(text) for text in filters):
            return False
        return any(_derives_from_conversation(gen.iter, held, readers) for gen in node.generators)
    return False


def _held_names(
    func: ast.AST, readers: set[str], inherited: set[str] | None = None
) -> set[str]:
    """Names in this function bound to a conversation read.

    Read IN SOURCE ORDER (`_own_nodes` yields that way) so a name is only held
    once the statement that binds it has been seen, and a chain of aliases
    resolves however many lines it is split over: `conv = _history(...)`, then
    `rows = conv["data"]`, then `assert rows[0]`.

    A nested function starts from the outer bindings it can see through the
    closure, but rebinding one of them — a parameter of the same name, `rows =
    [1, 2]` — ends it here too. Unioning the inherited set in afterwards would
    keep the outer conversation held under a name the inner function reuses.
    """
    held: set[str] = set(inherited) if inherited else set()
    # A parameter of the same name shadows the outer conversation just as a
    # rebinding does; the signature is not part of `_own_nodes`.
    args = getattr(func, "args", None)
    if isinstance(args, ast.arguments):
        for group in (args.posonlyargs, args.args, args.kwonlyargs, [args.vararg, args.kwarg]):
            for arg in group:
                if arg is not None:
                    held.discard(arg.arg)
    for node in _own_nodes(func):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            # A `for block in blocks` binds ONE block; it is not the timeline.
            continue
        if _derives_from_conversation(value, held, readers):
            for target in targets:
                held.update(name.id for name in ast.walk(target) if isinstance(name, ast.Name))
        else:
            # Rebound to something that is not the conversation any more —
            # `blocks = [b for b in blocks if b["kind"] == "message"]` says
            # which blocks it means, so the later reads are the test's own.
            for target in targets:
                held.difference_update(
                    name.id for name in ast.walk(target) if isinstance(name, ast.Name)
                )
    return held


def _source(node: ast.AST) -> str:
    return " ".join(ast.unparse(node).split())


def _own_nodes(func: ast.AST):
    """A function's own body, in source order, without the bodies of functions
    defined inside it.

    Source order matters: `_held_names` is one forward pass, so a name must not
    be seen before its binding statement. The stack therefore holds generators
    and each one is resumed where it left off, instead of pushing a reversed
    child list. `findings()` visits nested definitions on their own, and a name
    held in the outer function must not read as held in the inner one — the
    inner one sees it through the closure instead.
    """
    stack = [ast.iter_child_nodes(func)]
    while stack:
        try:
            node = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            stack.append(ast.iter_child_nodes(node))


def _findings_in_func(func: ast.AST, readers: set[str], held: set[str]) -> list[tuple[int, str, str]]:
    """The three shapes in one function, `held` already resolved by the caller."""
    found: list[tuple[int, str, str]] = []
    for node in _own_nodes(func):
        if isinstance(node, ast.Subscript) and _derives_from_conversation(node.value, held, readers):
            if _position(node.slice):
                found.append((node.lineno, "index", _source(node)))
        if isinstance(node, ast.Assert) and _asserts_nothing_is_there(node.test, held, readers):
            # `assert not _blocks(…)` is `== []` spelled as truthiness, and it
            # breaks the same way the day an announcement joins the timeline.
            found.append((node.lineno, "whole", _source(node.test)))
        if not isinstance(node, ast.Compare):
            continue
        if _is_whole_comparison(node, held, readers):
            found.append((node.lineno, "whole", _source(node)))
        for counted in _counted_operands(node, held, readers):
            found.append((node.lineno, "length", _source(counted)))
    return found


def _asserts_nothing_is_there(test: ast.AST, held: set[str], readers: set[str]) -> bool:
    """`assert not <conversation read>`: the empty-timeline assumption, as truthiness.

    Only the negated form. `assert blocks` says "something is there", which a
    new platform line cannot falsify.
    """
    return (
        isinstance(test, ast.UnaryOp)
        and isinstance(test.op, ast.Not)
        and _derives_from_conversation(test.operand, held, readers)
    )


def _is_pinned_number(node: ast.AST) -> bool:
    """A count written into the test: an integer literal, `True`/`False` included.

    `assert len(blocks) == True` pins a count just as `== 1` does, and
    `isinstance(True, int)` is True, so the two are one branch. A boolean field
    such as `page["has_more"] == False` never reaches here: that key is not the
    conversation, so the other side of the comparison is not counted at all.
    """
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, bool))


def _counted_operands(node: ast.Compare, held: set[str], readers: set[str]) -> list[ast.AST]:
    """Every side of an `==`/`!=` that pins a count of one conversation's blocks.

    Both sides are asked: `2 == len(rows)` pins the same number as the other
    spelling. So is every link of a chain — `len(a) == len(rows) == 1` pins the
    count at the middle link, where `node.comparators` puts it.
    """
    counted: list[ast.AST] = []
    for left, op, right in zip([node.left, *node.comparators], node.ops, node.comparators):
        if not isinstance(op, (ast.Eq, ast.NotEq)):
            continue
        for side, other in ((left, right), (right, left)):
            if not _is_pinned_number(other):
                continue
            expression = _counted_expression(side, held, readers)
            if expression is not None and expression not in counted:
                counted.append(expression)
    return counted


def _counted_expression(node: ast.AST, held: set[str], readers: set[str]) -> ast.AST | None:
    """`len(blocks)` — or `blocks` itself — when it is one conversation's blocks."""
    if isinstance(node, ast.Call) and _callee(node) == "len" and node.args:
        return node.args[0] if _derives_from_conversation(node.args[0], held, readers) else None
    return node if _derives_from_conversation(node, held, readers) else None


def _is_whole_comparison(node: ast.Compare, held: set[str], readers: set[str]) -> bool:
    """A conversation read is compared with something that is not one.

    A chain counts: `events == chat.events == rows == []` pins `rows` just as
    hard as `rows == []` does, and it is where the DB-side read in the tree is
    written. Every operator in the chain has to be equality — `len(blocks) < 2`
    waits for something to arrive instead of saying nothing else will.
    """
    if not all(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
        return False
    reads = [
        _derives_from_conversation(side, held, readers)
        for side in [node.left, *node.comparators]
    ]
    return any(reads) and not all(reads)


def findings(root: Path) -> tuple[Counter[tuple[str, str, str]], list[str]]:
    """Every rejection as (path, kind, source), counted, plus the files not parsed.

    Counted rather than set: a second copy of a frozen expression is a new
    violation, and it is exactly how this kind of assertion spreads.
    """
    rejected: Counter[tuple[str, str, str]] = Counter()
    unreadable: list[str] = []
    for path in sorted((root / "backend" / "tests").rglob("*.py")):
        try:
            tree = ast.parse(path.read_bytes(), filename=str(path))
        except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
            unreadable.append(f"{path}: {exc}")
            continue
        engine = _Readers()
        relative = path.relative_to(root).as_posix()
        for node, held, readers in _scopes(tree, _nested_functions(tree), engine):
            for _, kind, source in _findings_in_func(node, readers, held):
                rejected[(relative, kind, source)] += 1
    return rejected, unreadable


def _scopes(
    tree: ast.Module, module_scope: dict[str, ast.AST], engine: _Readers
) -> Iterator[tuple[ast.AST, set[str], frozenset[str]]]:
    """Every scope in the file, with the names it can see and the readers it knows.

    A function defined inside another one reads the outer bindings through the
    closure, so `blocks = _history(…)[\"data\"]` in the test is still the
    conversation inside `def check():`. Inheriting the outer set is what makes
    that visible; `lambda` bodies are scopes too, so `wait_until(lambda: …)`
    is judged rather than skipped.
    """

    # Answer every definition before judging any of them: a helper defined
    # later in the file is classified just as carefully as one defined earlier,
    # and `settle` then settles the helpers that delegate to each other.
    # `names()` skips `test_*` definitions, so they are asked directly.
    for func in _nested_functions(tree).values():
        engine.is_reader(func, module_scope)
    engine.settle()

    def walk(node: ast.AST, inherited: set[str], scope: dict[str, ast.AST]):
        held = inherited
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = {**scope, **_nested_functions(node)}
            readers = engine.names(scope)
            held = _held_names(node, readers, inherited)
            yield node, held, readers
        elif isinstance(node, (ast.Module, ast.Lambda)):
            yield node, held, engine.names(scope)
        for child in ast.iter_child_nodes(node):
            yield from walk(child, held, scope)

    yield from walk(tree, set(), module_scope)


# --------------------------------------------------------------------------- #
# Judging against the frozen baseline
# --------------------------------------------------------------------------- #


def compare(
    rejected: Counter[tuple[str, str, str]],
    baseline: tuple[tuple[str, str, str], ...],
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """(findings the baseline does not cover, frozen entries nothing matches).

    Multisets: a file that already holds one frozen expression and gains a
    second copy of it has one finding the baseline does not cover.
    """
    frozen = Counter(baseline)
    return sorted((rejected - frozen).elements()), sorted((frozen - rejected).elements())


def main() -> int:
    parser = argparse.ArgumentParser(description="Test conversation-shape guard")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dump", action="store_true", help="列出全部命中，不看基线")
    args = parser.parse_args()
    root = args.root.resolve()

    rejected, unreadable = findings(root)
    for line in unreadable:
        print(f"读不了这个测试文件，判断不了: {line}", file=sys.stderr)

    if args.dump:
        print(
            json.dumps(
                [f"{p}\t{k}\t{s}" for (p, k, s) in sorted(rejected.elements())],
                ensure_ascii=False,
                indent=0,
            )
        )
        return 2 if unreadable else 0

    new, stale = compare(rejected, BASELINE)
    if args.json:
        print(
            json.dumps(
                {
                    "new": [f"{path} {kind} — {source}" for path, kind, source in new],
                    "stale": [f"{path} {kind} — {source}" for path, kind, source in stale],
                    "unreadable": unreadable,
                },
                ensure_ascii=False,
            )
        )
    else:
        for path, kind, source in new:
            print(f"{path} {kind}: {HINTS[kind]}", file=sys.stderr)
            print(f"    这处：{source}", file=sys.stderr)
        for path, kind, source in stale:
            print(f"{path} {kind} — {source} 已经不在树里了，从 BASELINE 删掉这一行", file=sys.stderr)

    if unreadable:
        return 2
    if new or stale:
        print(f"对话形状守卫：新增 {len(new)} 处，基线过期 {len(stale)} 处", file=sys.stderr)
        return 1
    print("对话形状守卫通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
