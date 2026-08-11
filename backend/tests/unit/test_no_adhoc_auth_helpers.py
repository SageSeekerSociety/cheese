"""Guard: no test file may mint its own session token.

This is deliberately a *static* test, which is the one exception to the
"test behaviour, don't inspect source" rule in CLAUDE.md — the thing being
tested IS a property of the source tree.

Why it exists: ``.claude/rules/backend-tests.md`` used to carry the sentence
"eight files already carry a copy-pasted ``_auth()`` helper — don't add a
ninth". When this guard was written, 14 files under ``tests/integration``
minted their own session token: 9 through a local ``def _auth(...)`` (the
ninth arrived while the prose rule was in force), 3 through the same copy named
``_login``, and 2 with the mint call inlined into ``client.headers``. A rule
that only lives in prose gets broken silently; this test makes it fail loudly.

Banning the *name* ``_auth`` would have caught 9 of those 14 — every copy that
dodged the rule had already been renamed or inlined. So the guard sits on the
scarce resource instead: ``app.core.tokens.mint_session_token``. Call the shared
``tests.integration.conftest.session_token`` /
``tests.integration.conftest.session_auth_headers`` helpers instead.
"""

import ast
import pathlib

TESTS_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Files allowed to reach for mint_session_token directly, each for a stated
# reason. Keep this list short; "my test also needs a token" is not a reason.
ALLOWED = {
    # Defines the shared helpers everything else must use.
    "integration/conftest.py",
    # Tests mint_session_token itself (claims, TTL, round-trip).
    "unit/test_tokens.py",
    # Needs tokens carrying a numeric user_id, which the handle-only shared
    # helper deliberately does not expose.
    "unit/test_actor_numeric_handle.py",
}


def _mint_references(tree: ast.AST) -> list[int]:
    """Line numbers where ``mint_session_token`` is imported or accessed.

    Covers ``from app.core.tokens import mint_session_token`` (including
    ``as`` aliases, which would otherwise hide from a name-based scan) and
    attribute access such as ``tokens.mint_session_token(...)``.
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == "mint_session_token" for alias in node.names):
                lines.add(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == "mint_session_token":
            lines.add(node.lineno)
    return sorted(lines)


def test_no_test_file_mints_its_own_session_token() -> None:
    offenders: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        rel = path.relative_to(TESTS_ROOT).as_posix()
        if rel in ALLOWED:
            continue
        for lineno in _mint_references(ast.parse(path.read_text())):
            offenders.append(f"backend/tests/{rel}:{lineno}")

    assert not offenders, (
        "These test files mint their own session token instead of using the "
        "shared helper:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse `from tests.integration.conftest import session_auth_headers` "
        "(or `session_token` when you need the raw token). If your case genuinely "
        "cannot use them, extend the helper — or add the file to ALLOWED in "
        "backend/tests/unit/test_no_adhoc_auth_helpers.py WITH a reason."
    )


def test_allowlist_has_no_stale_entries() -> None:
    """A renamed/deleted allowlisted file must not rot silently into a hole."""
    missing = [rel for rel in sorted(ALLOWED) if not (TESTS_ROOT / rel).is_file()]
    assert not missing, (
        f"ALLOWED names files that no longer exist: {missing}. "
        "Drop them — every stale entry is a hole in the guard."
    )
