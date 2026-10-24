"""Which provider a project's turns run on (ExecutionProfile resolution).

The rules: a project runs on the platform pool unless it names a registered
profile that has credentials, and the platform registers no profile that hands
a session a provider credential of its own — the pool goes through the gateway,
where the payer's key is metered.
"""

from types import SimpleNamespace

from app.domain.agent.profiles import (
    TIER_DEFAULT,
    AgentProfile,
    ProfileRegistry,
    build_registry,
)

POOL_URL = "https://gw/anthropic"


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        agent_model="glm-5.2",
        anthropic_base_url=POOL_URL,
        anthropic_auth_token="pool-key",
        agent_sonnet_model="glm-5.2",
        agent_opus_model="glm-5.2",
        agent_haiku_model="glm-4.5-air",
    )


def test_unset_or_unknown_profile_runs_on_the_pool():
    reg = build_registry(_settings())
    for chosen in (None, {}, {"execution_profile": "nope"}):
        assert reg.resolve(chosen).name == "default"


def test_a_project_still_bound_to_a_removed_claude_profile_runs_on_the_pool():
    """`claude-opus` and `claude-fable` ran on a platform Claude credential
    straight at Anthropic, past the gateway and the metering proxy. A project
    whose settings still name one gets the pool, through the gateway."""
    reg = build_registry(_settings())
    for removed in ("claude-opus", "claude-fable"):
        env = reg.resolve({"execution_profile": removed}).full_env()
        assert env["ANTHROPIC_BASE_URL"] == POOL_URL
        assert env["ANTHROPIC_AUTH_TOKEN"] == "pool-key"
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""


def test_the_platform_offers_only_the_pool():
    reg = build_registry(_settings())
    assert [v.name for v in reg.selectable()] == ["default"]


def test_profile_without_credentials_is_not_available():
    dead = AgentProfile("x", "X", TIER_DEFAULT, "m", None, None)
    assert not dead.available
    reg = ProfileRegistry(
        [AgentProfile("default", "D", TIER_DEFAULT, "glm", None, "k"), dead],
        "default",
    )
    assert reg.resolve({"execution_profile": "x"}).name == "default"
    assert [v.name for v in reg.selectable()] == ["default"]


def test_full_env_replaces_provider_and_pins_subagent_models():
    byo = AgentProfile("byo", "B", "byo", "own-model", None, "k", haiku_model="h")
    env = byo.full_env()
    assert env["ANTHROPIC_AUTH_TOKEN"] == "k"
    # An unset base_url and the subscription token are written EMPTY: unset to
    # the CLI, but masking whatever the backend's own environment holds.
    assert env["ANTHROPIC_BASE_URL"] == ""
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "own-model"
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "own-model"
