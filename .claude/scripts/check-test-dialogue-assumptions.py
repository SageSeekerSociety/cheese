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

#: What a filter on the returned rows looks like, for a helper that narrows
#: inside itself instead of at its call site. Matched against `ast.unparse`,
#: which prints a string constant with single quotes.
ROW_KEYS = (
    "'kind'", ".kind", "'author'", ".author",
    "'meta'", ".meta", "event_type", "event_types", "BlockKind",
)

#: A conversation's blocks come from `/history` or `/transcript` under `/topics/`.
#: The app's other `/history` endpoints are a team's credit log or a document's
#: versions, which no announcement joins.
TOPIC_SEGMENTS = ("/history", "/transcript")

#: The patterns already in the tree, frozen. One entry per finding: the file
#: under `backend/tests/`, which of the three shapes, and the expression's
#: normalized source. Delete an entry only by fixing the test it names.
_THREAD = "backend/tests/integration/test_a_thread_reads_the_message_it_hangs_under.py"
_ROOM = "backend/tests/integration/test_chat_history.py"

BASELINE: tuple[tuple[str, str, str], ...] = (
    # A thread's conversation: each of these says the thread holds exactly the
    # blocks the test posted into it.
    (_THREAD, "whole", "[b['content'] for b in page['data']] == "
                       "['微信里打不开预览', 'uploads/spec.pdf']"),
    (_THREAD, "whole", "[b['id'] for b in found['data']] == [root['id']]"),
    (_THREAD, "whole", "[b['id'] for b in newest['data']] == [b['id'] for b in said[1:]]"),
    (_THREAD, "whole", "[b['id'] for b in older['data']] == [root['id'], said[0]['id']]"),
    (_THREAD, "whole", "[b['id'] for b in page['data']] == [root['id'], said['id']]"),
    (_THREAD, "whole", "[b['id'] for b in page['data']] == [root['id']]"),
    (_THREAD, "whole", "older_still['data'] == []"),
    # A room's history: pages, slices, and the one search by a payload.
    (_ROOM, "index", "_history(client, room, q='配置文件缺失')['data'][0]"),
    (_ROOM, "whole", "[b['id'] for b in data['data']] == ids[:1]"),
    (_ROOM, "whole", "[b['id'] for b in forward['data']] == ids[1:4]"),
    (_ROOM, "whole", "[b['id'] for b in latest['data']] == ids[-50:]"),
    (_ROOM, "whole", "[b['id'] for b in next_page['data']] == ids[4:7]"),
    (_ROOM, "whole", "[b['id'] for b in older['data']] == ids[:7]"),
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


def _reads_topic_conversation(node: ast.Call) -> bool:
    """`client.get(f"/topics/{topic}/history", …)` and the 现场 read beside it.

    The path has to END at the collection: `/history/{block_id}` reads one block
    the caller named by id, and a single block is not a conversation nobody
    else writes to.
    """
    for arg in node.args:
        text = _path_text(arg)
        if text and "/topics" in text and any(text.endswith(seg) for seg in TOPIC_SEGMENTS):
            return True
    return False


def _reads_blocks_of_one_conversation(node: ast.Call) -> bool:
    """A `Block` query pinned to one conversation: the DB side of the same read."""
    source = ast.unparse(node)
    return "select(Block)" in source and "conversation_id" in source


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


def _narrows_inside(func: ast.AST) -> bool:
    """A helper that filters the rows it hands back down to a kind/author/meta.

    Filtering down to the event slice does not count: those are the lines a
    change to what the platform says adds or removes.
    """
    for node in ast.walk(func):
        if not isinstance(node, (ast.Compare, ast.Call)):
            continue
        text = ast.unparse(node)
        if any(key in text for key in ROW_KEYS) and not _narrows_to_events(text):
            return True
    return False


def _readers(tree: ast.Module) -> set[str]:
    """Module-level helpers that return one conversation's blocks, unnarrowed.

    A helper counts only when the read is written inside it. Following helper
    calls instead would drag in every function that merely looks at a
    conversation on its way past (`_recorded` returns a run record and asserts
    about the conversation beside it), and those names say nothing about what
    they hand back.
    """
    readers: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("test_") or _narrows_inside(node):
            continue
        if any(
            isinstance(child, ast.Call)
            and (_reads_topic_conversation(child) or _reads_blocks_of_one_conversation(child))
            for child in ast.walk(node)
        ):
            readers.add(node.name)
    return readers


# --------------------------------------------------------------------------- #
# Finding the three shapes
# --------------------------------------------------------------------------- #

#: Calls that hand a payload on unchanged. Only calls, never bare attributes:
#: `response.status_code` is a field of the response, not blocks, and following
#: it turns every `assert response.status_code == 200` into a false positive.
WRAPPER_CALLS = frozenset({"json", "scalars", "all", "items", "list"})


def _derives_from_conversation(node: ast.AST, held: set[str], readers: set[str]) -> bool:
    """Is this expression a collection of one conversation's blocks?

    Widening rather than narrowing is what a guard must not do, so an
    unrecognised shape reads as "not blocks" and is left alone.
    """
    if isinstance(node, ast.Name):
        return node.id in held
    if isinstance(node, ast.Call):
        if _callee(node) in readers:
            return not _call_narrows(node)
        if _reads_topic_conversation(node) or _reads_blocks_of_one_conversation(node):
            return not _call_narrows(node)
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
        if isinstance(key, ast.Constant) and isinstance(key.value, int):
            # A block, not the collection. That subscript is the finding itself.
            return False
        return _derives_from_conversation(node.value, held, readers)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        # `[b for b in blocks if b.meta["tool"] == "Task"]` is the narrowing
        # written as a filter instead of a query parameter.
        filters = [ast.unparse(condition) for gen in node.generators for condition in gen.ifs]
        if any(
            any(key in text for key in ROW_KEYS) and not _narrows_to_events(text)
            for text in filters
        ):
            return False
        return any(_derives_from_conversation(gen.iter, held, readers) for gen in node.generators)
    return False


def _held_names(func: ast.AST, readers: set[str]) -> set[str]:
    """Names in this function bound to a conversation read."""
    held: set[str] = set()
    for node in _own_nodes(func):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign) and _derives_from_conversation(node.value, held, readers):
            targets = list(node.targets)
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and _derives_from_conversation(node.value, held, readers)
        ):
            targets = [node.target]
        elif isinstance(node, (ast.For, ast.AsyncFor)) and _derives_from_conversation(node.iter, held, readers):
            targets = [node.target]
        for target in targets:
            held.update(name.id for name in ast.walk(target) if isinstance(name, ast.Name))
    return held


def _source(node: ast.AST) -> str:
    return " ".join(ast.unparse(node).split())


def _own_nodes(func: ast.AST):
    """A function's own body, without the bodies of functions defined inside it.

    `findings()` visits nested definitions on their own, and a name held in the
    outer function must not read as held in the inner one.
    """
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(node))


def _findings_in_func(func: ast.AST, readers: set[str]) -> list[tuple[int, str, str]]:
    if not readers:
        return []
    held = _held_names(func, readers)
    found: list[tuple[int, str, str]] = []
    for node in _own_nodes(func):
        if isinstance(node, ast.Subscript) and _derives_from_conversation(node.value, held, readers):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, int):
                found.append((node.lineno, "index", _source(node)))
        if not isinstance(node, ast.Compare):
            continue
        if _is_whole_comparison(node, held, readers):
            found.append((node.lineno, "whole", _source(node)))
        for op, right in zip(node.ops, node.comparators):
            if not isinstance(op, (ast.Eq, ast.NotEq)):
                continue
            if not isinstance(right, ast.Constant) or not isinstance(right.value, int):
                continue
            counted = _counted_expression(node.left, held, readers)
            if counted is not None:
                found.append((node.lineno, "length", _source(counted)))
    return found


def _counted_expression(node: ast.AST, held: set[str], readers: set[str]) -> ast.AST | None:
    """`len(blocks)` — or `blocks` itself — when it is one conversation's blocks."""
    if isinstance(node, ast.Call) and _callee(node) == "len" and node.args:
        return node.args[0] if _derives_from_conversation(node.args[0], held, readers) else None
    return node if _derives_from_conversation(node, held, readers) else None


def _is_whole_comparison(node: ast.Compare, held: set[str], readers: set[str]) -> bool:
    """One side is a conversation read, the other is not a read of the same one."""
    if len(node.ops) != 1 or not isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
        return False
    left, right = node.left, node.comparators[0]
    left_reads = _derives_from_conversation(left, held, readers)
    right_reads = _derives_from_conversation(right, held, readers)
    return left_reads != right_reads


def findings(root: Path) -> tuple[set[tuple[str, str, str]], list[str]]:
    """Every rejection as (path, kind, source), plus the files not parsed."""
    rejected: set[tuple[str, str, str]] = set()
    unreadable: list[str] = []
    for path in sorted((root / "backend" / "tests").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            unreadable.append(f"{path}: {exc}")
            continue
        readers = _readers(tree)
        if not readers:
            continue
        relative = str(path.relative_to(root))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for _, kind, source in _findings_in_func(node, readers):
                    rejected.add((relative, kind, source))
    return rejected, unreadable


# --------------------------------------------------------------------------- #
# Judging against the frozen baseline
# --------------------------------------------------------------------------- #


def compare(
    rejected: set[tuple[str, str, str]], baseline: tuple[tuple[str, str, str], ...]
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """(findings the baseline does not cover, frozen entries nothing matches)."""
    frozen = set(baseline)
    return sorted(rejected - frozen), sorted(frozen - rejected)


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
        print(json.dumps([f"{p}\t{k}\t{s}" for (p, k, s) in sorted(rejected)], ensure_ascii=False, indent=0))
        return 0

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
