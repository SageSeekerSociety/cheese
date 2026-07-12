"""ExecutionProfile resolution + compliance guard (design §2 / review Finding 7)."""

from app.domain.agent.profiles import (
    TIER_DEFAULT,
    TIER_TESTING,
    AgentProfile,
    ProfileRegistry,
)


def _registry(dogfood: set[str] = frozenset()) -> ProfileRegistry:
    pool = AgentProfile(
        name="default",
        label="Pool",
        tier=TIER_DEFAULT,
        model="glm-5.2",
        base_url="https://gw/anthropic",
        auth_token="pool-key",
        haiku_model="glm-4.5-air",
    )
    opus = AgentProfile(
        name="claude-opus",
        label="Opus",
        tier=TIER_TESTING,
        model="claude-opus-4-8",
        base_url=None,
        auth_token="anthropic-key",
        haiku_model="claude-opus-4-8",
    )
    return ProfileRegistry([pool, opus], "default", frozenset(dogfood))


def test_unset_resolves_to_default():
    reg = _registry()
    assert reg.resolve(None).name == "default"
    assert reg.resolve({}).name == "default"
    assert reg.resolve({"execution_profile": "nope"}).name == "default"


def test_testing_profile_blocked_for_non_dogfood_owner():
    reg = _registry(dogfood={"andyl"})
    sel = {"execution_profile": "claude-opus"}
    # A normal project owner cannot run the testing profile → falls back to pool.
    assert reg.resolve(sel, owner_handle="student-1").name == "default"
    # A dogfood owner can.
    assert reg.resolve(sel, owner_handle="andyl").name == "claude-opus"


def test_selectable_hides_testing_from_non_dogfood():
    reg = _registry(dogfood={"andyl"})
    names_student = {v.name for v in reg.selectable("student-1")}
    names_andyl = {v.name for v in reg.selectable("andyl")}
    assert names_student == {"default"}
    assert names_andyl == {"default", "claude-opus"}


def test_profile_without_credentials_is_not_available():
    dead = AgentProfile("x", "X", TIER_DEFAULT, "m", None, None)
    assert not dead.available
    reg = ProfileRegistry(
        [
            AgentProfile("default", "D", TIER_DEFAULT, "glm", None, "k"),
            dead,
        ],
        "default",
    )
    assert reg.resolve({"execution_profile": "x"}).name == "default"


def test_full_env_replaces_provider_and_pins_subagent_models():
    opus = AgentProfile(
        "claude-opus", "O", TIER_TESTING, "claude-opus-4-8", None, "k", haiku_model="h"
    )
    env = opus.full_env()
    assert env["ANTHROPIC_AUTH_TOKEN"] == "k"
    # None base_url now yields an explicit EMPTY value: unset to the CLI, but
    # masking any inherited gateway URL (leak-hijack fix).
    assert env["ANTHROPIC_BASE_URL"] == ""
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "claude-opus-4-8"
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "claude-opus-4-8"


def test_build_registry_has_fable_channel():
    """The Fable dogfooding channel: same seat credentials as claude-opus, its
    own model, selectable only by dogfood owners."""
    from types import SimpleNamespace

    from app.domain.agent.profiles import build_registry

    settings = SimpleNamespace(
        agent_model="glm-5.2",
        anthropic_base_url="https://gw/anthropic",
        anthropic_auth_token="pool-key",
        agent_sonnet_model="glm-5.2",
        agent_opus_model="glm-5.2",
        agent_haiku_model="glm-4.5-air",
        claude_model="claude-opus-4-8",
        claude_base_url=None,
        claude_auth_token=None,
        claude_oauth_token="oauth-tok",
        fable_model="claude-fable-5",
        dogfood_owner_handles=["andyl"],
        agent_default_profile="default",
    )
    reg = build_registry(settings)
    fable = reg.get("claude-fable")
    assert fable is not None and fable.tier == TIER_TESTING
    assert fable.model == "claude-fable-5"
    assert fable.full_env()["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-tok"
    assert fable.full_env()["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "claude-fable-5"
    # Dogfood owner sees it; an outsider doesn't.
    assert any(v.name == "claude-fable" for v in reg.selectable("andyl"))
    assert not any(v.name == "claude-fable" for v in reg.selectable("stranger"))
    # A non-dogfood project pointing at fable falls back to the pool.
    pick = {"execution_profile": "claude-fable"}
    assert reg.resolve(pick, "stranger").name == "default"
    assert reg.resolve(pick, "andyl").name == "claude-fable"
