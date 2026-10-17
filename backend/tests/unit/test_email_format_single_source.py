"""The email address format is compiled once, in ``app/core/email.py``.

Sign-up, password recovery and sign-in all decide whether an address is well
formed. When one of them carries its own copy of the pattern, a change to the
shared rule (a length cap, a tighter domain) leaves the copy accepting what the
rest reject: an address can be registered that signing in then refuses, and a
recovery mail can go to an address the other entrances no longer take.

Two checks, because neither alone covers the case: a copy whose source still
equals the shared one passes the identity check (``re.compile`` hands back the
same object for an identical source) and is caught by the scan, and a copy whose
source has since drifted fails identity but no longer spells the shared literal,
so the scan misses it. That is why the scan's literal is read from the shared
object rather than written out here — a third copy of the rule would retire
itself the moment the rule changed.
"""

import ast
from pathlib import Path

from app.api.routes import users_password
from app.api.routes.users import _common, registration
from app.core import email as core_email

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
HOME = APP_ROOT / "core" / "email.py"

# What a copy would be made of: the shared rule itself, not a second copy of it.
PATTERN = core_email.EMAIL_FORMAT.pattern


def spelled_out(source: str) -> list[int]:
    """Lines that spell the pattern out instead of importing it."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and node.value == PATTERN
    ]


def test_every_entrance_uses_the_one_pattern() -> None:
    assert registration.EMAIL_FORMAT is core_email.EMAIL_FORMAT
    assert users_password.EMAIL_FORMAT is core_email.EMAIL_FORMAT
    # The users package re-exports it under the name its routes import.
    assert _common._EMAIL_FORMAT is core_email.EMAIL_FORMAT


def test_no_other_module_spells_the_pattern_out() -> None:
    offenders = {
        path.relative_to(APP_ROOT).as_posix(): lines
        for path in sorted(APP_ROOT.rglob("*.py"))
        # An address pattern cannot name an address without an "@"; skip the
        # rest of the tree rather than parsing it.
        if path != HOME
        and "@" in (text := path.read_text())
        and (lines := spelled_out(text))
    }
    assert offenders == {}, (
        "Import EMAIL_FORMAT from app.core.email instead of compiling the "
        f"pattern again: {offenders}"
    )


def test_guard_sees_a_local_copy() -> None:
    assert spelled_out(f'PATTERN = r"{PATTERN}"\n') == [1]
    assert spelled_out('PATTERN = r"^[^@]+@[^@]+$"\n') == []
