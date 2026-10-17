"""Dump the backend's settings — env name, type, default, and the comment above
each field — from backend/app/core/config.py, as JSON.

Parsed, not imported: importing the settings module validates a live
environment, and the reference must build on a machine that has none.

The page claims to list them ALL, so a base class `Settings` is given by the
repository itself is read too, and every field says which file it came from:
`config.py` is held to a line cap and puts a section in a module of its own
rather than grow past it (`core/sandbox_config`), and a field declared there is
still a field `Settings` reads. Only bases imported from `app` are followed;
everything else is a library, not this repository's own settings.
"""

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OWN_PACKAGE = "app."
CONFIG = ROOT / "backend" / "app" / "core" / "config.py"


def read(path: Path) -> tuple[list[str], ast.Module]:
    source = path.read_text(encoding="utf-8")
    return source.splitlines(), ast.parse(source)


def own_imports(tree: ast.Module) -> dict[str, str]:
    """Local name -> module, for every name imported from this repository."""
    found = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(OWN_PACKAGE):
            for alias in node.names:
                found[alias.asname or alias.name] = node.module
    return found


def source_of(module: str) -> Path:
    """`app.core.sandbox_config` -> the file holding it, under `backend/`."""
    return ROOT / "backend" / Path(*module.split(".")).with_suffix(".py")


def comment_above(lines: list[str], lineno: int) -> str:
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


def fields_of(cls: ast.ClassDef, lines: list[str], origin: str) -> list[dict]:
    out = []
    for node in cls.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            out.append({
                "name": name,
                "env": env_names(node) or [name.upper()],
                "type": ast.unparse(node.annotation),
                "default": default_of(node),
                "doc": comment_above(lines, node.lineno),
                "line": node.lineno,
                "origin": origin,
            })
    return out


def classes(tree: ast.Module) -> dict[str, ast.ClassDef]:
    return {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}


def settings_fields() -> list[dict]:
    """Every field on `Settings`, own and inherited, in the file each sits in."""
    lines, tree = read(CONFIG)
    settings = classes(tree)["Settings"]
    out = fields_of(settings, lines, CONFIG.relative_to(ROOT).as_posix())
    imports = own_imports(tree)
    for base in settings.bases:
        name = ast.unparse(base)
        path = source_of(imports[name]) if name in imports else None
        if path is None or not path.exists():
            continue
        base_lines, base_tree = read(path)
        if name in classes(base_tree):
            origin = path.relative_to(ROOT).as_posix()
            out.extend(fields_of(classes(base_tree)[name], base_lines, origin))
    return out


fields = settings_fields()
json.dump({"file": "backend/app/core/config.py", "fields": fields}, sys.stdout, ensure_ascii=False)
