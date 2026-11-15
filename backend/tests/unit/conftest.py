import sys

import pytest

from app.core import policy

# The macOS sandbox tests need `sandbox-exec`, which only a Mac has. CI runs on
# Linux and admits no skipped case (`scripts/assert_suite_ran.py`), so off a
# Mac the module is not collected at all.
collect_ignore = [] if sys.platform == "darwin" else ["test_device_sandbox_macos.py"]


@pytest.fixture(autouse=True)
def _nobody_oversees(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests judge each kind's own rule, mostly over fake sessions. Who is
    a platform admin (``policy.oversee_with``) is registered when ``app.main``
    is imported, which another test in the same process may have done; the
    registered judge reads tables a fake session does not have. The platform
    admin's oversight is tested end to end in the integration suite."""
    monkeypatch.setattr(policy, "_overseer", None)
