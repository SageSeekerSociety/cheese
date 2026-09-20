"""FEEDBACK_ADMIN_HANDLES must fail closed when a deployment names nobody.

``/admin/feedback`` is opened by exactly one thing: the ``feedback_admin_handles``
settings list. There is no role behind it (nothing in the product assigns
``SystemRole.SUPER_ADMIN``), no default member set, and no way to promote
yourself from the UI. So an empty list on a deployment is not "we have not
appointed an admin yet" — it is a feedback queue that accepts submissions and
can never be worked, and nothing inside the running system can tell that apart
from "nobody has picked this up yet":

- a submitter watches their report sit at 已收录 with no way to know it is not
  going to move;
- the operator opens the admin page and reads "你的账号不在管理员名单里", a
  sentence that names the wrong problem;
- the agent proposal path keeps spending its daily quota on proposals nobody
  can act on.

The fix is the same shape as #338/#439's JWT guard: make the silently-degrading
case a loud, boot-time event, and decide "is this a deployment" from the same
two signals (``deployed_via_compose`` first, then ``environment``).

Local dev and the test suite set neither signal, so they keep the empty default
and the whole feedback feature stays usable there — only the admin console is
unreachable, which is the correct local state.
"""

from pathlib import Path

import pytest

from app.core.config import Settings

# Every deployment case has to get past the JWT guard first (it is declared
# before this one and runs first), so a real secret is part of the fixture, not
# the thing under test.
_REAL_SECRET = "a-genuinely-random-48-char-secret-value"


def _build(**overrides: object) -> Settings:
    # _env_file=None keeps a stray .env from smuggling handles in and masking the
    # case under test; init kwargs already outrank env vars in pydantic-settings,
    # so what is passed here is what actually applies.
    return Settings(_env_file=None, **overrides)


@pytest.mark.parametrize("environment", ["production", "staging", "dev"])
def test_deployment_rejects_an_empty_admin_list(environment: str) -> None:
    # RuntimeError, not pydantic's ValidationError: the boot must die on this one
    # message without pydantic dumping the input dict — which on a real deploy
    # carries live secrets — into the crash log (same reason as the JWT guard).
    with pytest.raises(RuntimeError) as excinfo:
        _build(environment=environment, jwt_secret=_REAL_SECRET)
    message = str(excinfo.value)
    assert "FEEDBACK_ADMIN_HANDLES" in message
    assert "/admin/feedback" in message


def test_compose_deployment_rejects_it_even_when_environment_says_development() -> None:
    # The #439 lesson: ENVIRONMENT lives in the box's env file, so the very
    # window this guard exists for — the env file not applying — can also make a
    # deployment read as "development". The compose literal cannot fall back.
    with pytest.raises(RuntimeError) as excinfo:
        _build(
            environment="development",
            # A real secret, so the JWT guard lets this through and the refusal
            # under test is this one rather than that one.
            jwt_secret=_REAL_SECRET,
            deployed_via_compose=True,
        )
    assert "FEEDBACK_ADMIN_HANDLES" in str(excinfo.value)


@pytest.mark.parametrize("environment", ["development", "test"])
def test_local_and_test_keep_the_empty_default(environment: str) -> None:
    # No handles are configured locally or in CI, and the suite boots the whole
    # app — fail-closed must not reach here.
    settings = _build(environment=environment, jwt_secret="dev-secret")
    assert settings.feedback_admin_handles == []


def test_local_dev_is_unaffected_by_the_compose_signal() -> None:
    settings = _build(environment="development", jwt_secret="dev-secret")
    assert settings.deployed_via_compose is False
    assert settings.feedback_admin_handles == []


def test_deployment_with_named_admins_boots() -> None:
    settings = _build(
        environment="production",
        jwt_secret=_REAL_SECRET,
        feedback_admin_handles=["alice", "bob"],
    )
    assert settings.feedback_admin_handles == ["alice", "bob"]


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_entry_is_rejected_everywhere(blank: str) -> None:
    # Worse than a missing entry: the list is non-empty, so the deployment guard
    # would wave it through, and the name it lists is one no account can ever
    # authenticate as. Local dev is not exempt — a typo is a typo.
    with pytest.raises(RuntimeError) as excinfo:
        _build(
            environment="development",
            jwt_secret="dev-secret",
            feedback_admin_handles=[blank, "alice"],
        )
    assert "FEEDBACK_ADMIN_HANDLES" in str(excinfo.value)


def test_prod_template_advertises_the_requirement() -> None:
    # The guard is only as useful as the operator's knowledge that it exists. The
    # prod template is where "REQUIRED" lives for this deployment, so pin that it
    # names the key — a template that silently drops it turns a boot failure into
    # a mystery.
    template = (
        Path(__file__).resolve().parents[3] / "deploy" / ".env.prod.example"
    ).read_text(encoding="utf-8")
    keys = {
        line.split("=", 1)[0].strip()
        for line in template.splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    }
    assert "FEEDBACK_ADMIN_HANDLES" in keys
