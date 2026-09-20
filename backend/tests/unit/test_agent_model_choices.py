"""Explicit model selection never silently delegates to the CLI default."""

import pytest

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.agent.market import subscription_model_alias
from app.domain.agent_instance.configuration import (
    AgentConfiguration,
    harness_choices,
    initial_model,
    model_choices,
    models_for,
    validate_configuration,
)


@pytest.mark.parametrize(
    ("chosen", "requested"),
    [
        ("sonnet", "claude-sonnet-5"),
        ("opus", "claude-opus-5"),
        ("opus-4.8", "claude-opus-4-8"),
        ("fable", "claude-fable-5"),
    ],
)
def test_model_is_explicit(chosen, requested):
    assert subscription_model_alias(chosen) == requested


@pytest.mark.parametrize("chosen", [None, "", "unknown"])
def test_unknown_model_is_not_a_default(chosen):
    with pytest.raises(ValidationError):
        subscription_model_alias(chosen)


@pytest.mark.parametrize(
    ("enabled", "supply", "expected"),
    [
        (True, None, "sonnet"),
        (False, None, "gateway-model"),
        (True, "gateway", "gateway-model"),
        (True, "subscription", "sonnet"),
    ],
)
def test_choices_and_validation_follow_project_supply(
    monkeypatch, enabled, supply, expected
):
    monkeypatch.setattr(settings, "subscription_enabled", enabled)
    monkeypatch.setattr(settings, "agent_model", "gateway-model")
    project = {"supply": supply}
    assert initial_model(project) == expected
    assert expected in {item["id"] for item in model_choices(project)}
    validate_configuration(AgentConfiguration(model=expected), project)
    with pytest.raises(ValidationError):
        validate_configuration(AgentConfiguration(model="unknown"), project)


def test_subscription_models_are_unavailable_without_subscription_transport(
    monkeypatch,
):
    monkeypatch.setattr(settings, "subscription_enabled", False)
    project = {"supply": "subscription"}
    assert {"glm-5.2", "deepseek-flash"} <= {
        item["id"] for item in model_choices(project)
    }
    assert initial_model(project) == settings.agent_model
    with pytest.raises(ValidationError, match="请选择可用模型"):
        validate_configuration(AgentConfiguration(model="sonnet"), project)


@pytest.mark.parametrize("supply", ["subscription", "gateway"])
def test_agents_can_select_each_gateway_model_without_changing_project_supply(
    monkeypatch, supply
):
    monkeypatch.setattr(settings, "subscription_enabled", True)
    project = {"supply": supply}
    for model in ("glm-5.2", "deepseek-flash", "sonnet"):
        validate_configuration(AgentConfiguration(model=model), project)
    assert sum(item["default"] for item in model_choices(project)) == 1


def test_a_harness_is_offered_only_where_it_has_something_to_drive(monkeypatch):
    """A deployment that serves no model a harness supports does not have that
    harness, whatever the registry says. Offering it produces a teammate that
    cannot take a turn."""
    monkeypatch.setattr(settings, "agent_harness_models", {})
    offered = {item["id"] for item in harness_choices({})}
    assert "codex" not in offered, "Codex brings its own list and none was named"
    # The two that speak the platform gateway need no list to be usable.
    assert {"claude-code", "pi"} <= offered


def test_a_harness_that_speaks_the_gateway_drives_every_model_the_project_has(
    monkeypatch,
):
    """It reaches models through the same gateway the project's own pool is,
    so restating which ones would be a second list to fall out of date."""
    monkeypatch.setattr(settings, "subscription_enabled", False)
    everything = {item["id"] for item in model_choices({})}
    for harness in ("claude-code", "pi"):
        assert set(models_for(harness, {})) == everything
        for model in everything:
            validate_configuration(AgentConfiguration(model=model, harness=harness), {})


def test_a_harness_drives_only_what_was_named_for_it(monkeypatch):
    monkeypatch.setattr(settings, "agent_harness_models", {"codex": ["codex-fixture"]})
    validate_configuration(
        AgentConfiguration(model="codex-fixture", harness="codex"), {}
    )
    with pytest.raises(ValidationError):
        validate_configuration(AgentConfiguration(model="glm-5.2", harness="codex"), {})
    # And a model named for one harness is not thereby withheld from the rest:
    # it reaches the same gateway.
    validate_configuration(AgentConfiguration(model="codex-fixture"), {})


def test_refusing_a_pair_names_the_harness_rather_than_the_model(monkeypatch):
    """The two halves fail for different reasons, and naming the wrong one
    sends a person to change the half they chose on purpose. This read as
    「当前项目无法使用模型 X」 while the model was perfectly available."""
    monkeypatch.setattr(settings, "agent_harness_models", {"codex": ["codex-fixture"]})
    with pytest.raises(ValidationError) as refusal:
        validate_configuration(AgentConfiguration(model="glm-5.2", harness="codex"), {})
    assert "Codex" in str(refusal.value)
    assert "glm-5.2" in str(refusal.value)


def test_an_unknown_harness_is_refused_as_a_harness(monkeypatch):
    with pytest.raises(ValidationError, match="运行方式"):
        validate_configuration(
            AgentConfiguration(model="glm-5.2", harness="nothing-we-run"), {}
        )


def test_a_subscription_model_stays_with_the_harness_its_credential_is_for(
    monkeypatch,
):
    """The credential, not the request shape: a subscription turn authenticates
    with something minted for one harness, so listing the alias among another
    harness's API models must not make it selectable there."""
    monkeypatch.setattr(settings, "subscription_enabled", True)
    monkeypatch.setattr(
        settings, "agent_harness_models", {"codex": ["sonnet", "codex-fixture"]}
    )
    for harness in ("codex", "pi"):
        assert "sonnet" not in models_for(harness, {})
        with pytest.raises(ValidationError):
            validate_configuration(
                AgentConfiguration(model="sonnet", harness=harness), {}
            )
    validate_configuration(AgentConfiguration(model="sonnet"), {})


def test_a_new_agent_starts_on_a_model_its_harness_can_drive(monkeypatch):
    """A preset that asks for a harness of its own would otherwise start
    pointed at the project's default model and be refused on the way in, for a
    combination nobody chose."""
    monkeypatch.setattr(settings, "subscription_enabled", True)
    monkeypatch.setattr(settings, "agent_harness_models", {"codex": ["codex-fixture"]})
    project = {"supply": "subscription"}
    # The project's own default is a subscription model Codex cannot carry.
    assert initial_model(project) == "sonnet"
    assert initial_model(project, "codex") == "codex-fixture"
    for harness in ("claude-code", "codex", "pi"):
        validate_configuration(
            AgentConfiguration(model=initial_model(project, harness), harness=harness),
            project,
        )
