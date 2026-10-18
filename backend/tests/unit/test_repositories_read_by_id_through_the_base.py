"""A repository on ``Repository`` reads by id through the base, not by hand.

Two rules over ``backend/app``:

1. Every class in ``ON_BASE`` subclasses ``app.domain.repositories.Repository``.
   The list only grows: each repository moved onto the base is added here, so
   it cannot quietly move back off it.
2. A subclass of ``Repository`` (direct, aliased or through another subclass)
   does not read its own ``model`` by id by hand: no ``Model.id == x`` or
   ``self.model.id == x``, no ``.__eq__``, no ``.in_`` over an id list written
   in place. Its reads by id are the inherited ``get_by_id`` / ``get_by_ids``;
   a filter every read needs goes in ``base_statement``. ``.in_`` over a
   subquery filters by another table and is allowed.

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


def _base_names(node: ast.expr) -> str | None:
    target = node.value if isinstance(node, ast.Subscript) else node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _model_name(node: ast.ClassDef) -> str | None:
    for stmt in node.body:
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets = [stmt.target]
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == "model" for t in targets):
            if isinstance(stmt.value, ast.Name):
                return stmt.value.id
    return None


def _is_model(node: ast.expr, model: str | None) -> bool:
    if isinstance(node, ast.Name):
        return node.id == model
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "model"
        and isinstance(node.value, ast.Name)
        and node.value.id in {"self", "cls"}
    )


def _is_model_id(node: ast.expr, model: str | None) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "id"
        and _is_model(node.value, model)
    )


def _a_literal_list(call: ast.Call) -> bool:
    # ``Model.id.in_(subquery)`` filters by another table and stays; an id
    # list written out in place is a batch read by hand.
    return len(call.args) == 1 and isinstance(
        call.args[0], (ast.List, ast.Tuple, ast.Set, ast.ListComp)
    )


def _hand_reads(node: ast.ClassDef, model: str | None) -> list[int]:
    lines: list[int] = []
    for inner in ast.walk(node):
        if isinstance(inner, ast.Compare):
            sides = [inner.left, *inner.comparators]
            if any(isinstance(op, ast.Eq) for op in inner.ops) and any(
                _is_model_id(side, model) for side in sides
            ):
                lines.append(inner.lineno)
        elif (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and _is_model_id(inner.func.value, model)
            and (
                inner.func.attr == "__eq__"
                or (inner.func.attr == "in_" and _a_literal_list(inner))
            )
        ):
            lines.append(inner.lineno)
    return lines


def judge(sources: dict[str, str], on_base: tuple[tuple[str, str], ...]) -> list[str]:
    problems: list[str] = []
    classes: dict[tuple[str, str], ast.ClassDef] = {}
    roots = {"Repository"}
    for path, text in sorted(sources.items()):
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ClassDef):
                classes[(path, node.name)] = node
            elif isinstance(node, ast.ImportFrom):
                roots.update(
                    alias.asname
                    for alias in node.names
                    if alias.name == "Repository" and alias.asname
                )
    # Subclasses of subclasses count too: grow the set of names that are on
    # the base until it stops growing, keeping the model each one inherits.
    on_base_names = set(roots)
    models: dict[str, str | None] = {}
    grew = True
    while grew:
        grew = False
        for (_path, name), node in classes.items():
            bases = [_base_names(base) for base in node.bases]
            if name in models or not any(b in on_base_names for b in bases):
                continue
            inherited = next((models[b] for b in bases if b in models), None)
            models[name] = _model_name(node) or inherited
            on_base_names.add(name)
            grew = True
    for path, name in on_base:
        if (path, name) not in classes or name not in models:
            problems.append(f"{path}::{name} 不再继承 Repository")
    for (path, name), node in classes.items():
        if name not in models:
            continue
        model = models[name]
        for line in _hand_reads(node, model):
            problems.append(
                f"{path}:{line} {name} 手写了按 {model or 'model'}.id 的读，"
                "改用继承的 get_by_id / get_by_ids，共用过滤放进 base_statement"
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
    "annotated model and self.model": (
        {
            "r.py": (
                "class TagRepository(Repository[Tag]):\n"
                "    model: type[Tag] = Tag\n"
                "    def f(self, x):\n"
                "        a = select(Tag).where(Tag.id == x)\n"
                "        return select(Tag).where(self.model.id == x)\n"
            )
        },
        (),
        2,
    ),
    "in_ and __eq__ on the model id": (
        {
            "r.py": (
                "class TagRepository(Repository[Tag]):\n"
                "    model = Tag\n"
                "    def f(self, x):\n"
                "        a = select(Tag).where(Tag.id.in_([x]))\n"
                "        b = select(Tag).where(Tag.id.in_(subquery))\n"
                "        return select(Tag).where(Tag.id.__eq__(x))\n"
            )
        },
        (),
        2,
    ),
    "aliased, module-qualified and indirect bases": (
        {
            "a.py": (
                "from app.domain.repositories import Repository as Base\n"
                "class A(Base[Tag]):\n"
                "    model = Tag\n"
                "    q = Tag.id == 1\n"
            ),
            "b.py": (
                "class B(repositories.Repository[Tag]):\n"
                "    model = Tag\n"
                "    q = Tag.id == 1\n"
                "class C(B):\n"
                "    q = Tag.id == 2\n"
            ),
        },
        (("b.py", "C"),),
        3,
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
