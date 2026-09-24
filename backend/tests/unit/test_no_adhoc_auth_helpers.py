"""Guard: no test file may mint its own handle-only token.

This is deliberately a *static* test, which is the one exception to the
"test behaviour, don't inspect source" rule in CLAUDE.md — the thing being
tested IS a property of the source tree.

Why it exists: ``.claude/rules/backend-tests.md`` used to carry the sentence
"eight files already carry a copy-pasted ``_auth()`` helper — don't add a
ninth". When this guard was written, 14 *files* under ``tests/integration``
minted their own handle-only token: 9 through a local ``def _auth(...)`` (the
ninth arrived while the prose rule was in force), 3 through the same copy named
``_login``, and 2 with the mint call inlined into ``client.headers``.  A rule
that only lives in prose gets broken silently; this test makes it fail loudly.

Counting by name does not even measure the right thing, in either direction:
``_login`` in ``test_connector_device_flow.py`` and ``_login_real`` in
``test_connector_viewer.py`` are *not* copies of this — they seed a real DB user
for a numeric-sub token — and ``_authenticated_project_owner`` is an ``_auth``
prefix that is also something else. The name says nothing about whether the
file is minting.

Banning the *name* ``_auth`` would have caught 9 of those 14 — every copy that
dodged the rule had already been renamed or inlined. So the guard sits on the
scarce resource instead: a call to ``create_access_token`` that names no user
id. Call the shared ``tests.integration.conftest.session_token`` /
``tests.integration.conftest.session_auth_headers`` helpers instead.
"""

import ast
import pathlib

TESTS_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Files allowed to mint a handle-only token directly, each for a stated reason.
# Keep this list short; "my test also needs a token" is not a reason.
ALLOWED = {
    # Defines the shared helpers everything else must use.
    "integration/conftest.py",
    # Tests what a handle-only token is and is not accepted as.
    "unit/test_auth_service.py",
}


def _names_no_user(call: ast.Call) -> bool:
    def is_none(node: ast.expr) -> bool:
        return isinstance(node, ast.Constant) and node.value is None

    if call.args and is_none(call.args[0]):
        return True
    return any(kw.arg == "user_id" and is_none(kw.value) for kw in call.keywords)


def _handle_only_mints(tree: ast.AST) -> list[int]:
    """Line numbers of ``create_access_token(None, ...)`` calls, however the
    function was reached (a bare name, an ``as`` alias, or ``auth.``)."""
    aliases = {"create_access_token"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "create_access_token" and alias.asname:
                    aliases.add(alias.asname)
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name in aliases and _names_no_user(node):
            lines.add(node.lineno)
    return sorted(lines)


def test_no_test_file_mints_its_own_handle_only_token() -> None:
    offenders: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        rel = path.relative_to(TESTS_ROOT).as_posix()
        if rel in ALLOWED:
            continue
        for lineno in _handle_only_mints(ast.parse(path.read_text())):
            offenders.append(f"backend/tests/{rel}:{lineno}")

    assert not offenders, (
        "These test files mint their own handle-only token instead of using the "
        "shared helper:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse `from tests.integration.conftest import session_auth_headers` "
        "(or `session_token` when you need the raw token). If your case genuinely "
        "cannot use them, extend the helper — or add the file to ALLOWED in "
        "backend/tests/unit/test_no_adhoc_auth_helpers.py WITH a reason."
    )


def test_the_guard_sees_the_shared_helper() -> None:
    """If the helper stops minting through ``create_access_token(None, ...)``,
    this guard is watching the wrong call and passes vacuously."""
    conftest = TESTS_ROOT / "integration" / "conftest.py"
    assert _handle_only_mints(ast.parse(conftest.read_text())), (
        "tests/integration/conftest.py no longer mints handle-only tokens with "
        "create_access_token(None, ...); point this guard at what it uses now."
    )


def test_allowlist_has_no_stale_entries() -> None:
    """A renamed/deleted allowlisted file must not rot silently into a hole."""
    missing = [rel for rel in sorted(ALLOWED) if not (TESTS_ROOT / rel).is_file()]
    assert not missing, (
        f"ALLOWED names files that no longer exist: {missing}. "
        "Drop them — every stale entry is a hole in the guard."
    )
