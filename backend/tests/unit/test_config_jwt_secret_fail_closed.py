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

from pathlib import Path

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


# --- #439: the guard must not depend on the file it is guarding ----------------
#
# ENVIRONMENT and JWT_SECRET both come from the box's env file, so the very window
# this guard exists for — the env file not applying (#342/#356) — takes out both:
# the secret falls back to `dev-secret` AND environment falls back to
# `development`, so the check above waves the deployment through. The compose file
# asserts DEPLOYED_VIA_COMPOSE as a literal, which cannot fall back with it.


@pytest.mark.parametrize("secret", ["dev-secret", "", "   "])
def test_compose_deployment_rejects_insecure_secret_despite_dev_environment(
    secret: str,
) -> None:
    # The regression this is really about: environment reads "development" NOT
    # because this is a dev box, but because the env file failed to load. Trusting
    # it here is what let a deployment boot on the in-source default.
    with pytest.raises(RuntimeError) as excinfo:
        _build(environment="development", jwt_secret=secret, deployed_via_compose=True)
    message = str(excinfo.value)
    assert "JWT_SECRET" in message
    assert "#342" in message


def test_compose_deployment_with_a_real_secret_boots() -> None:
    settings = _build(
        environment="development",
        jwt_secret="a-genuinely-random-48-char-secret-value",
        deployed_via_compose=True,
    )
    assert settings.deployed_via_compose is True


@pytest.mark.parametrize("secret", ["dev-secret", "", "   "])
def test_local_dev_is_unaffected_by_the_compose_signal(secret: str) -> None:
    # Nothing outside the compose file sets it, so a developer's machine and the
    # suite keep booting on the default exactly as before.
    settings = _build(environment="development", jwt_secret=secret)
    assert settings.deployed_via_compose is False


def test_compose_file_asserts_the_signal_as_a_literal() -> None:
    # The guard above is only as good as this line existing, and being a constant.
    # An interpolated value (${...}) would inherit the weakness it exists to
    # remove: unset upstream, it resolves empty and the guard turns itself off.
    compose = (
        Path(__file__).resolve().parents[3]
        / "deploy"
        / "compose"
        / "docker-compose.base.yml"
    )
    import yaml

    services = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]
    for service in ("backend", "device-connection"):
        environment = services[service]["environment"]
        assert "DEPLOYED_VIA_COMPOSE=1" in environment, (
            f"{service} must assert DEPLOYED_VIA_COMPOSE as a literal; "
            f"found {environment!r}"
        )
