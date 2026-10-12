"""The database-session and signed-in-user dependencies are defined once.

A route module that rebuilds `Annotated[AsyncSession, Depends(get_db)]` under its
own name (`DbSession`, `Db`, ...) is one more copy that other route modules then
borrow from it, and each borrow is a route importing a route. The only definition
is `app/api/request_deps.py`; everything else imports it.
"""

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
HOME = APP_ROOT / "api" / "request_deps.py"

# (type name, dependency function) of each alias that has one home.
SHARED = {("AsyncSession", "get_db"), ("AuthUserInfo", "require_auth_user")}


def _is_annotated_depends(value: ast.expr) -> bool:
    if not (isinstance(value, ast.Subscript) and isinstance(value.value, ast.Name)):
        return False
    if value.value.id != "Annotated" or not isinstance(value.slice, ast.Tuple):
        return False
    elts = value.slice.elts
    if len(elts) != 2 or not isinstance(elts[0], ast.Name):
        return False
    dep = elts[1]
    if not (
        isinstance(dep, ast.Call)
        and isinstance(dep.func, ast.Name)
        and dep.func.id == "Depends"
        and len(dep.args) == 1
        and isinstance(dep.args[0], ast.Name)
    ):
        return False
    return (elts[0].id, dep.args[0].id) in SHARED


def redefined_aliases(source: str) -> list[str]:
    """Names a module binds at top level to one of the shared dependency aliases."""
    return [
        target.id
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign) and _is_annotated_depends(node.value)
        for target in node.targets
        if isinstance(target, ast.Name)
    ]


def test_a_module_that_rebuilds_the_shared_aliases_is_found():
    source = (
        "from typing import Annotated\n"
        "from fastapi import Depends\n"
        "Db = Annotated[AsyncSession, Depends(get_db)]\n"
        "Me = Annotated[AuthUserInfo, Depends(require_auth_user)]\n"
        "Other = Annotated[AsyncSession, Depends(get_other)]\n"
    )
    assert redefined_aliases(source) == ["Db", "Me"]


def test_only_request_deps_defines_the_shared_aliases():
    offenders = {
        str(path.relative_to(APP_ROOT)): names
        for path in sorted(APP_ROOT.rglob("*.py"))
        if path != HOME and (names := redefined_aliases(path.read_text()))
    }
    assert offenders == {}, (
        "import DbSession / AuthUser from app.api.request_deps instead of "
        f"defining another alias: {offenders}"
    )
    assert redefined_aliases(HOME.read_text()) == ["DbSession", "AuthUser"]
