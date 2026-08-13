"""JWT_SECRET must fail closed when a deployment is left at the insecure default.

Signing tokens with the built-in ``dev-secret`` default on a real deployment is
the root cause of #342: an env-not-applied deploy window boots one process on the
default, and when the pinned real secret returns, every token signed with
``dev-secret`` in between fails verification and every user is silently logged out
(24/24 401 in #342). The fix makes the backend refuse to construct its settings —
i.e. refuse to boot — in that situation.

These tests pin the boundary functionally, by constructing ``Settings`` and
observing whether it accepts the value:

- a deployment (``environment`` outside development/test) with a missing/empty/
  default secret MUST raise, so the process crash-loops loudly instead of quietly
  invalidating sessions later;
- local development and the test environment MUST keep accepting the default,
  because the whole suite signs real tokens with it — if fail-closed reached them
  it would take every DB-backed test down with it.
"""

import pytest

from app.core.config import Settings


def _build(**overrides: str) -> Settings:
    # _env_file=None keeps a stray .env from smuggling a real secret in and
    # masking the case under test; init kwargs already outrank env vars in
    # pydantic-settings, so the values passed here are what actually apply.
    return Settings(_env_file=None, **overrides)


@pytest.mark.parametrize("environment", ["production", "staging", "dev"])
@pytest.mark.parametrize("bad_secret", ["dev-secret", "", "   "])
def test_deployment_rejects_insecure_jwt_secret(
    environment: str, bad_secret: str
) -> None:
    # RuntimeError (not pydantic's ValidationError): the boot must die on the one
    # message without pydantic dumping the input dict — which on a real deploy
    # carries live secrets — into the crash log.
    with pytest.raises(RuntimeError) as excinfo:
        _build(environment=environment, jwt_secret=bad_secret)
    message = str(excinfo.value)
    assert "JWT_SECRET" in message
    assert "#342" in message


def test_boot_failure_does_not_leak_other_secrets() -> None:
    # The refusal must not print sibling settings (DB password, API tokens) the
    # way a wrapped pydantic ValidationError would — its repr dumps the whole
    # input dict, and a real deployment's is full of live secrets (#338: keys
    # never hit logs). microcloud_tenant_secret is a plain (un-aliased) field, so
    # this canary genuinely lands in the model and would surface in that dump.
    with pytest.raises(RuntimeError) as excinfo:
        _build(
            environment="production",
            jwt_secret="",
            microcloud_tenant_secret="LEAK-CANARY-SECRET",
        )
    assert "LEAK-CANARY-SECRET" not in str(excinfo.value)


@pytest.mark.parametrize("environment", ["development", "test"])
@pytest.mark.parametrize("secret", ["dev-secret", "", "   "])
def test_local_and_test_keep_the_default(environment: str, secret: str) -> None:
    # No real secret is configured for local dev or CI, and both sign real tokens
    # with the default — they MUST keep booting.
    settings = _build(environment=environment, jwt_secret=secret)
    assert settings.environment == environment


def test_deployment_with_a_real_secret_boots() -> None:
    settings = _build(
        environment="production", jwt_secret="a-genuinely-random-48-char-secret-value"
    )
    assert settings.jwt_secret == "a-genuinely-random-48-char-secret-value"
