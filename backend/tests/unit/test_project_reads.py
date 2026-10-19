"""One project-or-404 read, shared by every service that needs a project row.

The rule is a product one: a project that is not there answers 404 with the
``projectNotFound`` catalog key, in both languages. The three lines that do it
were copied into four service helpers, so moving the key or adding a parameter
meant finding all four. They now call ``project_or_404``.

The last test is the ratchet. Behaviour tests only pin the one copy that exists
today; a fifth private copy would pass every behaviour test and still leave the
rule spelled in two places for the next change to miss. It reads the source
instead, and its own self-tests prove it sees the shape it claims to see.
"""

import ast
import uuid
from pathlib import Path

import pytest

from app.core.errors import NotFoundError
from app.domain.project.reads import project_or_404

APP = Path(__file__).resolve().parents[2] / "app"


class _Repo:
    """The one method ``project_or_404`` asks a repository for."""

    def __init__(self, row: object | None) -> None:
        self.row = row
        self.asked: list[uuid.UUID] = []

    async def get(self, project_id: uuid.UUID) -> object | None:
        self.asked.append(project_id)
        return self.row


async def test_the_project_that_is_there_comes_back_unchanged():
    row = object()
    repo = _Repo(row)
    project_id = uuid.uuid4()

    assert await project_or_404(repo, project_id) is row
    assert repo.asked == [project_id]


async def test_a_missing_project_is_the_catalog_refusal():
    with pytest.raises(NotFoundError) as raised:
        await project_or_404(_Repo(None), uuid.uuid4())

    body = raised.value.to_response_body()
    assert body["error"]["i18n"]["key"] == "projectNotFound"


# --- the ratchet ---------------------------------------------------------


def _is_keyed_project_refusal(node: ast.AST) -> bool:
    """``raise NotFoundError(say("projectNotFound"))``."""
    if not isinstance(node, ast.Raise):
        return False
    call = node.exc
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "NotFoundError"
        and len(call.args) == 1
        and isinstance(call.args[0], ast.Call)
        and isinstance(call.args[0].func, ast.Name)
        and call.args[0].func.id == "say"
        and [arg.value for arg in call.args[0].args if isinstance(arg, ast.Constant)]
        == ["projectNotFound"]
    )


def _none_guard_name(test: ast.expr) -> str | None:
    """``<name> is None`` -> ``<name>``; anything else -> None."""
    if (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Is)
        and isinstance(test.left, ast.Name)
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
    ):
        return test.left.id
    return None


def _service_fetch_name(stmt: ast.stmt) -> str | None:
    """``row = await self.<repo>.get(<one positional argument>)`` -> ``row``.

    Only a method reading through its own repository counts. A route's
    ``ProjectRepository(db).get(project_id)`` and the session's
    ``session.get(Project, project_id)`` are different reads with different
    callers, and neither is the private-helper shape this guards.
    """
    if not (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
        and isinstance(stmt.value, ast.Await)
        and isinstance(stmt.value.value, ast.Call)
    ):
        return None
    call = stmt.value.value
    receiver = call.func.value if isinstance(call.func, ast.Attribute) else None
    if not (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "get"
        and isinstance(receiver, ast.Attribute)
        and isinstance(receiver.value, ast.Name)
        and receiver.value.id == "self"
        and len(call.args) == 1
        and not call.keywords
    ):
        return None
    return stmt.targets[0].id


def inline_project_or_404_copies(source: str) -> list[int]:
    """Line numbers of ``if <row> is None: raise projectNotFound`` after a fetch."""
    found: list[int] = []

    def scan(body: list[ast.stmt]) -> None:
        for previous, current in zip(body, body[1:], strict=False):
            fetched = _service_fetch_name(previous)
            if fetched is None or not isinstance(current, ast.If):
                continue
            if _none_guard_name(current.test) != fetched:
                continue
            if any(_is_keyed_project_refusal(n) for n in ast.walk(current)):
                found.append(current.lineno)
        for stmt in body:
            for field in ("body", "orelse", "finalbody"):
                scan(getattr(stmt, field, []) or [])
            for handler in getattr(stmt, "handlers", []) or []:
                scan(handler.body)
            for case in getattr(stmt, "cases", []) or []:
                scan(case.body)

    scan(ast.parse(source).body)
    return found


def test_no_service_keeps_its_own_project_or_404():
    offenders = {
        str(path.relative_to(APP)): lines
        for path in sorted(APP.rglob("*.py"))
        if (lines := inline_project_or_404_copies(path.read_text(encoding="utf-8")))
    }

    assert offenders == {}, (
        "call app.domain.project.reads.project_or_404 instead of copying the "
        f"fetch-and-refuse into a service helper: {offenders}"
    )


_COPIED = """
class Service:
    async def _project(self, project_id):
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError(say("projectNotFound"))
        return project
"""


def test_the_guard_finds_the_copy_it_exists_to_stop():
    assert len(inline_project_or_404_copies(_COPIED)) == 1


@pytest.mark.parametrize(
    "source",
    [
        _COPIED.replace("projectNotFound", "topicNotFound"),
        _COPIED.replace(
            "await self._projects.get(project_id)",
            "await self._session.get(Project, project_id)",
        ),
        _COPIED.replace(
            "project = await self._projects.get(project_id)",
            "project = await project_or_404(self._projects, project_id)",
        ),
        '"""raise NotFoundError(say("projectNotFound")) after a fetch."""',
    ],
)
def test_the_guard_leaves_every_other_shape_alone(source):
    assert inline_project_or_404_copies(source) == []
