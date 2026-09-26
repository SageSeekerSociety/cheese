"""Dump the backend's settings — env name, type, default, and the comment above
each field — from backend/app/core/config.py, as JSON.

Parsed, not imported: importing the settings module validates a live
environment, and the reference must build on a machine that has none.
"""

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
path = ROOT / "backend" / "app" / "core" / "config.py"
source = path.read_text(encoding="utf-8")
lines = source.splitlines()
tree = ast.parse(source)


def comment_above(lineno: int) -> str:
    out = []
    i = lineno - 2
    while i >= 0 and lines[i].strip().startswith("#"):
        out.append(lines[i].strip().lstrip("#").strip())
        i -= 1
    return " ".join(reversed(out)).strip()


def env_names(node) -> list[str]:
    names = []
    call = node.value if isinstance(node.value, ast.Call) else None
    if call:
        for kw in call.keywords:
            if kw.arg in ("validation_alias", "alias"):
                for sub in ast.walk(kw.value):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        names.append(sub.value)
    return names


def default_of(node) -> str:
    value = node.value
    if value is None:
        return "（必填）"
    if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "Field":
        for kw in value.keywords:
            if kw.arg == "default":
                return ast.unparse(kw.value)
        if value.args:
            return ast.unparse(value.args[0])
        return "（见代码）"
    return ast.unparse(value)


fields = []
for cls in tree.body:
    if isinstance(cls, ast.ClassDef) and cls.name == "Settings":
        for node in cls.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name = node.target.id
                fields.append({
                    "name": name,
                    "env": env_names(node) or [name.upper()],
                    "type": ast.unparse(node.annotation),
                    "default": default_of(node),
                    "doc": comment_above(node.lineno),
                    "line": node.lineno,
                })

json.dump({"file": "backend/app/core/config.py", "fields": fields}, sys.stdout, ensure_ascii=False)
