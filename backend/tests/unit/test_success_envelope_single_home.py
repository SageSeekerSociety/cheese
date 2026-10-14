"""Guard: the ``code: 200`` success envelope comes from ``ok()``, nowhere else.

A static test, the one exception to "test behaviour, don't inspect source" in
AGENTS.md: the property under test is a property of the source tree.

When this guard was written, 205 routes spelled ``{"code": 200, ...}`` out by
hand beside 449 calls to ``ok()``. Sixteen of the hand-written ones had already
drifted from the envelope a client reads: eleven carried no ``data`` and five no
``message``, and ``message`` came in four spellings ("OK", "ok", "Success",
"success"). ``ok()`` always writes all three fields, so a route that uses it
cannot answer with a different shape.

Forty-three routes still spell out a ``{"code": 201, ...}`` envelope, with
messages of their own. ``ok()`` writes 200 only, so this guard does not see
them; they are the next change, and it widens when they are gone.
"""

import ast
import pathlib

APP_ROOT = pathlib.Path(__file__).resolve().parents[2] / "app"

# The one place the envelope is spelled out.
HOME = "api/response.py"


def _hand_written_envelopes(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
        if "code" not in keys or not {"message", "data"} & set(keys):
            continue
        code = node.values[keys.index("code")]
        if isinstance(code, ast.Constant) and code.value == 200:
            lines.append(node.lineno)
    return lines


def test_200_success_envelopes_come_from_ok() -> None:
    offenders = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        relative = path.relative_to(APP_ROOT).as_posix()
        if relative == HOME:
            continue
        for line in _hand_written_envelopes(ast.parse(path.read_text())):
            offenders.append(f"app/{relative}:{line}")
    assert not offenders, (
        "Build the 200 success envelope with app.api.response.ok() instead of a "
        "{'code': 200, ...} literal:\n" + "\n".join(offenders)
    )


def test_guard_sees_a_hand_written_envelope() -> None:
    tree = ast.parse('def f():\n    return {"code": 200, "message": "OK"}\n')
    assert _hand_written_envelopes(tree) == [2]
