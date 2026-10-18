"""A repository on ``Repository`` reads by id through the base, not by hand.

Two rules over ``backend/app``:

1. Every class in ``ON_BASE`` subclasses ``app.domain.repositories.Repository``.
   The list only grows: each repository moved onto the base is added here, so
   it cannot quietly move back off it.
2. A subclass of ``Repository`` does not compare its own ``model``'s ``id`` with
   ``==`` (the hand-written ``select(Model).where(Model.id == x)``). Its
   single read is the inherited ``get_by_id``; a filter every read needs goes
   in ``base_statement``.

The scan reads source, not imports, so it sits in ``TREE_GUARDS``.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

ON_BASE = (
    ("domain/avatars/repositories.py", "AvatarRepository"),
    ("domain/comments/repositories.py", "CommentRepository"),
    ("domain/groups/repositories.py", "GroupRepository"),
    ("domain/tag/repositories.py", "TagRepository"),
)


def _on_base(node: ast.ClassDef) -> bool:
    for base in node.bases:
        target = base.value if isinstance(base, ast.Subscript) else base
        if isinstance(target, ast.Name) and target.id == "Repository":
            return True
    return False


def _model_name(node: ast.ClassDef) -> str | None:
    for stmt in node.body:
        if (
            isinstance(stmt, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "model" for t in stmt.targets)
            and isinstance(stmt.value, ast.Name)
        ):
            return stmt.value.id
    return None


def _is_model_id(node: ast.expr, model: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "id"
        and isinstance(node.value, ast.Name)
        and node.value.id == model
    )


def judge(sources: dict[str, str], on_base: tuple[tuple[str, str], ...]) -> list[str]:
    problems: list[str] = []
    classes: dict[tuple[str, str], ast.ClassDef] = {}
    for path, text in sorted(sources.items()):
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ClassDef):
                classes[(path, node.name)] = node
    for path, name in on_base:
        node = classes.get((path, name))
        if node is None or not _on_base(node):
            problems.append(f"{path}::{name} 不再继承 Repository")
    for (path, name), node in classes.items():
        model = _model_name(node) if _on_base(node) else None
        if model is None:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Compare) or len(inner.ops) != 1:
                continue
            if not isinstance(inner.ops[0], ast.Eq):
                continue
            sides = (inner.left, inner.comparators[0])
            if any(_is_model_id(side, model) for side in sides):
                problems.append(
                    f"{path}:{inner.lineno} {name} 手写了 {model}.id == …，"
                    "改用继承的 get_by_id，共用过滤放进 base_statement"
                )
    return problems


def _tree() -> dict[str, str]:
    return {
        path.relative_to(APP).as_posix(): path.read_text(encoding="utf-8")
        for path in APP.rglob("*.py")
    }


def test_repositories_on_the_base_read_by_id_through_it():
    problems = judge(_tree(), ON_BASE)
    assert problems == [], "\n".join(problems)


_BITES = {
    "hand single read on the base": (
        {
            "r.py": (
                "class TagRepository(Repository[Tag]):\n"
                "    model = Tag\n"
                "    async def get_by_id(self, tag_id):\n"
                "        return select(Tag).where(Tag.id == tag_id)\n"
            )
        },
        (),
        1,
    ),
    "id on the right-hand side": (
        {
            "r.py": (
                "class TagRepository(Repository):\n"
                "    model = Tag\n"
                "    def f(self, x):\n"
                "        return select(Tag).where(x == Tag.id)\n"
            )
        },
        (),
        1,
    ),
    "listed class moved off the base": (
        {"r.py": "class TagRepository:\n    pass\n"},
        (("r.py", "TagRepository"),),
        1,
    ),
    "listed class missing": ({}, (("r.py", "TagRepository"),), 1),
    "other models and other columns pass": (
        {
            "r.py": (
                "class TagRepository(Repository[Tag]):\n"
                "    model = Tag\n"
                "    def f(self, x):\n"
                "        return select(Other).where(Other.id == x, Tag.name == x)\n"
            )
        },
        (("r.py", "TagRepository"),),
        0,
    ),
    "classes off the base are not judged": (
        {"r.py": "class Old:\n    model = Tag\n    q = Tag.id == 1\n"},
        (),
        0,
    ),
}


def test_the_guard_still_bites_on_its_own_cases():
    ran = 0
    for label, (sources, on_base, expected) in _BITES.items():
        assert len(judge(sources, on_base)) == expected, label
        ran += 1
    assert ran == len(_BITES) > 0
