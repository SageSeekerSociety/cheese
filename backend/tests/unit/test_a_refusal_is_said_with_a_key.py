"""A refusal is said with ``say()``, never written out in Chinese where it is raised.

An error raised with a ``say()`` sentence goes out with its key beside the
Chinese ``message`` (``error.i18n``), and the reader's screen renders it in
the language they picked (``docs/i18n.md``). One raised with Chinese written
into the call reaches an English reader in Chinese, and nothing fails when it
is written. This scan makes it fail.

An exception constructor here is the call a ``raise`` raises, ``HTTPException``,
any call to a class named ``*Error`` / ``*Exception``, and any class in the app
that inherits from one of those. Its arguments may not hold Chinese text
outside a ``say(...)``: a literal, an f-string, a concatenation, a
``.format()``. The one way out is a comment, ``# i18n-exempt: <why>``, at the
end of the line of the text or of the call, or alone on the line just above
the call (a reason seldom fits beside the code within the line length). One
without a reason fails too.
"""

import ast
import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "backend/app"

CJK = re.compile(r"[　-〿㐀-䶿一-鿿豈-﫿＀-￯]")
EXEMPT = re.compile(r"#\s*i18n-exempt\b(?P<rest>.*)")

#: Calls whose arguments are a sentence's parameters, not its text.
SAYING = {"say", "listing"}

HOW_TO_FIX = (
    "Chinese text raised as an error. Add the sentence to both "
    "frontend/src/i18n/messages/zh-CN/apiError.json and "
    "frontend/src/i18n/messages/en/apiError.json under one key, and raise "
    "say('<key>', **params) instead (app/domain/block/notice_text.py; "
    "docs/i18n.md). If this text truly is not shown to a person, put "
    "'# i18n-exempt: <reason>' on its line."
)


def _name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_error_name(name: str | None) -> bool:
    return name is not None and (
        name.endswith(("Error", "Exception")) or name == "BaseException"
    )


def _error_classes(trees: dict[Path, ast.Module]) -> set[str]:
    """Classes in the app that inherit, at any depth, from an exception."""
    bases: dict[str, set[str]] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases.setdefault(node.name, set()).update(
                    n for b in node.bases if (n := _name(b))
                )
    errors = {name for name in bases if _is_error_name(name)}
    grew = True
    while grew:
        found = {
            name
            for name, parents in bases.items()
            if name not in errors
            and any(_is_error_name(p) or p in errors for p in parents)
        }
        errors |= found
        grew = bool(found)
    return errors


def _cjk_nodes(node: ast.AST) -> list[ast.Constant]:
    """Chinese string constants in ``node``, outside any ``say(...)``."""
    if isinstance(node, ast.Call) and _name(node.func) in SAYING:
        return []
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str) and CJK.search(node.value):
            return [node]
        return []
    return [hit for child in ast.iter_child_nodes(node) for hit in _cjk_nodes(child)]


def _constructors(tree: ast.Module, errors: set[str]) -> list[ast.Call]:
    calls: dict[int, ast.Call] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and _name(node.exc.func) not in SAYING
        ):
            calls[id(node.exc)] = node.exc
        elif isinstance(node, ast.Call):
            name = _name(node.func)
            if name == "HTTPException" or _is_error_name(name) or name in errors:
                calls[id(node)] = node
    return list(calls.values())


def _comments(source: str) -> dict[int, tuple[str, bool]]:
    """line -> (comment, whether it is alone on its line)."""
    found = {}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            alone = token.line.strip().startswith("#")
            found[token.start[0]] = (token.string, alone)
    return found


def scan(paths: list[Path]) -> tuple[list[str], list[str]]:
    """(violations, exemptions without a reason), as ``file:line`` lines."""
    sources = {path: path.read_text("utf-8") for path in paths}
    trees = {path: ast.parse(source) for path, source in sources.items()}
    errors = _error_classes(trees)
    violations, unexplained = [], []
    for path, tree in trees.items():
        where = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        comments = _comments(sources[path])
        for line, (comment, _) in comments.items():
            exempt = EXEMPT.search(comment)
            if exempt and not exempt["rest"].lstrip(" :").strip():
                unexplained.append(f"{where}:{line}: {comment.strip()}")
        # A comment alone on its line exempts the line below it.
        exempt_lines = {
            line + alone
            for line, (comment, alone) in comments.items()
            if EXEMPT.search(comment)
        }
        lines = sources[path].splitlines()
        for call in _constructors(tree, errors):
            arguments = [*call.args, *(k.value for k in call.keywords)]
            for hit in (h for a in arguments for h in _cjk_nodes(a)):
                if {hit.lineno, call.lineno} & exempt_lines:
                    continue
                violations.append(
                    f"{where}:{hit.lineno}: {lines[hit.lineno - 1].strip()}"
                )
    return sorted(set(violations)), unexplained


def _app_files() -> list[Path]:
    return sorted(APP.rglob("*.py"))


def test_no_error_is_raised_with_chinese_written_into_it():
    violations, _ = scan(_app_files())
    assert violations == [], HOW_TO_FIX + "\n" + "\n".join(violations)


def test_every_exemption_says_why():
    _, unexplained = scan(_app_files())
    assert unexplained == [], (
        "An i18n exemption needs its reason on the same line: "
        "'# i18n-exempt: <why this text is not shown to a person>'.\n"
        + "\n".join(unexplained)
    )


def _scan_text(tmp_path: Path, source: str) -> tuple[list[str], list[str]]:
    path = tmp_path / "sample.py"
    path.write_text(source, "utf-8")
    return scan([path])


def test_the_scan_catches_each_way_chinese_reaches_an_error(tmp_path):
    source = """
from fastapi import HTTPException
from app.core.errors import BaseError, ConflictError

class Refused(BaseError):
    pass

class Worse(Refused):
    pass

def f(name, say):
    raise ConflictError("已经有了")
    raise ConflictError(f"{name} 已经有了")
    raise ConflictError("已经" + name)
    raise ConflictError("{} 已经有了".format(name))
    raise HTTPException(status_code=409, detail="已经有了")
    raise ValueError("不对")
    error = Worse("不行")
    raise Exception("不行")
"""
    violations, _ = _scan_text(tmp_path, source)
    assert [v.split(":")[1] for v in violations] == [
        "12",
        "13",
        "14",
        "15",
        "16",
        "17",
        "18",
        "19",
    ]


def test_a_sentence_and_plain_english_pass(tmp_path):
    source = """
from app.core.errors import ConflictError
from app.domain.block.notice_text import listing, say

def f(names):
    raise ConflictError(say("inviteSelf"))
    raise ConflictError(say("soleTopicOwner", topics=listing(["话题"], quoted=True)))
    raise ConflictError("already there")
    print("不是错误")
"""
    assert _scan_text(tmp_path, source) == ([], [])


def test_an_exemption_needs_a_reason(tmp_path):
    source = """
def f():
    raise ValueError("不对")  # i18n-exempt: matched by the upstream CLI parser
    raise ValueError("不对")  # i18n-exempt
    raise ValueError("不对")  # i18n-exempt:
    # i18n-exempt: runs where the catalog is not shipped
    raise ValueError("不对")
    # i18n-exempt: only the line just below is exempt
    x = 1
    raise ValueError("不对")
"""
    violations, unexplained = _scan_text(tmp_path, source)
    assert [v.split(":")[1] for v in violations] == ["10"]
    assert [u.split(":")[1] for u in unexplained] == ["4", "5"]
