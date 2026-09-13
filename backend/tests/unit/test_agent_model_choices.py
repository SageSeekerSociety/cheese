"""Explicit model selection never silently delegates to the CLI default."""

import pytest

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.agent.market import subscription_model_alias
from app.domain.agent_instance.configuration import (
    AgentConfiguration,
    initial_model,
    model_choices,
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


def test_codex_models_require_the_matching_harness(monkeypatch):
    monkeypatch.setattr(settings, "agent_codex_models", ["codex-fixture"])
    validate_configuration(
        AgentConfiguration(model="codex-fixture", harness="codex"), {}
    )
    for config in (
        AgentConfiguration(model="codex-fixture"),
        AgentConfiguration(model="glm-5.2", harness="codex"),
    ):
        with pytest.raises(ValidationError):
            validate_configuration(config, {})


def test_codex_models_are_not_advertised_without_configured_supply(monkeypatch):
    monkeypatch.setattr(settings, "agent_codex_models", [])
    assert all("codex" not in item["harnesses"] for item in model_choices({}))
