"""Platform model roles: the gateway models the platform picks by itself (the
default model, voice input, topic naming...), pointed from the admin page.

The rules a person can state without reading the code:

* A role nobody pointed reads the deployment's setting, so nothing changes
  until someone points one.
* Pointing a role takes effect without a redeploy, is audited, and moves the
  gateway key minted for that job to the new model, which the key would
  otherwise refuse.
* A role can only point at a model that can serve it, and a model a role runs
  on cannot be paused or deleted until the role is repointed; the page lists
  the roles on each model.
* A project's own settings still win.
"""

import pytest

from app.core import model_roles
from app.core.config import settings
from app.core.errors import BadRequestError, NotFoundError
from app.domain.agent.gateway_models import roles
from app.domain.agent.model_catalog import subagent_default
from app.domain.agent.schemas import ModelCreate, ModelUpdate
from app.domain.policy import gate
from tests.unit.test_gateway_model_sources import (
    _KIMI_CONFIG,
    _clean_module_state,  # noqa: F401 - autouse: a fresh gateway cache per test
    _Gateway,
    _listed,
    _service,
    _StubDb,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _no_roles_pointed():
    model_roles.reset()
    yield
    model_roles.reset()


_PAID = {"prices": {"input": 1e-6, "output": 2e-6}}


async def _models(service) -> None:
    """kimi-k3 (config, offered), an offered runtime model and an unlisted one."""
    await service.add(
        handle="admin",
        payload=ModelCreate(
            name="glm", upstream_model="anthropic/glm-5.2", selectable=True, **_PAID
        ),
    )
    await service.add(
        handle="admin",
        payload=ModelCreate(name="asr", upstream_model="qwen-asr/flash", **_PAID),
    )
    reset_cache()


@pytest.mark.anyio
async def test_a_role_reads_the_setting_until_pointed_and_again_once_cleared():
    gw = _Gateway([_KIMI_CONFIG])
    db = _StubDb()
    service = _service(gw, db)
    await _models(service)

    assert model_roles.get("voice_asr_model") == settings.voice_asr_model
    await service.set_role(handle="admin", role="voice_asr_model", model="asr")
    assert model_roles.get("voice_asr_model") == "asr"

    await service.set_role(handle="admin", role="voice_asr_model", model=None)
    assert model_roles.get("voice_asr_model") == settings.voice_asr_model
    listed = {r["role"]: r for r in (await service.roles())["roles"]}
    assert listed["voice_asr_model"]["chosen"] is False


@pytest.fixture
def narrowings(monkeypatch):
    """The narrowing the service schedules for later, run when the test says."""
    scheduled: list = []
    monkeypatch.setattr(
        roles.background, "spawn", lambda coro, name=None: scheduled.append(coro)
    )
    monkeypatch.setattr(roles, "NARROW_MARGIN_SECONDS", 0)
    monkeypatch.setattr(roles.settings, "platform_model_roles_refresh_interval_s", 0)
    return scheduled


@pytest.mark.anyio
async def test_pointing_a_role_moves_its_jobs_key_to_the_new_model(
    monkeypatch, narrowings
):
    """The key minted for voice input allows one model; left alone it refuses
    every call on the new one. It allows both until every process has read the
    new choice, since one still on the old model would be refused meanwhile."""
    gw = _Gateway([_KIMI_CONFIG])
    gw.keys["voice-asr"] = [settings.voice_asr_model]
    db = _StubDb()
    service = _service(gw, db)
    await _models(service)

    await service.set_role(handle="admin", role="voice_asr_model", model="asr")
    assert gw.keys["voice-asr"] == [settings.voice_asr_model, "asr"]

    for later in narrowings:
        await later
    assert gw.keys["voice-asr"] == ["asr"]


@pytest.mark.anyio
async def test_a_role_is_audited_with_what_it_was_and_what_it_became():
    gw = _Gateway([_KIMI_CONFIG])
    db = _StubDb()
    service = _service(gw, db)
    await _models(service)

    await service.set_role(handle="admin", role="agent_model", model="glm")

    audit = db.added[-1]
    assert (audit.action, audit.target, audit.result) == (
        "role.set",
        "agent_model",
        "ok",
    )
    assert audit.before == {"model": settings.agent_model}
    assert audit.after == {"model": "glm"}


@pytest.mark.anyio
async def test_a_role_points_only_at_a_model_that_can_serve_it():
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)
    await _models(service)
    await service.set_blocked(handle="admin", name="asr", blocked=True)
    reset_cache()

    # The default model is offered to every project, so it must be offered.
    with pytest.raises(BadRequestError):
        await service.set_role(handle="admin", role="agent_model", model="asr")
    # Voice input may run on an unlisted model, not on a paused one.
    with pytest.raises(BadRequestError):
        await service.set_role(handle="admin", role="voice_asr_model", model="asr")
    with pytest.raises(NotFoundError):
        await service.set_role(handle="admin", role="voice_asr_model", model="nope")
    with pytest.raises(NotFoundError):
        await service.set_role(handle="admin", role="no_such_role", model="glm")
    assert model_roles.get("agent_model") == settings.agent_model


@pytest.mark.anyio
async def test_a_model_a_role_runs_on_says_so_and_cannot_be_paused_or_deleted():
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)
    await _models(service)
    await service.set_role(handle="admin", role="voice_asr_model", model="asr")
    reset_cache()
    assert (await _listed(service, "asr"))["roles"] == ["voice_asr_model"]
    before = len(gw.writes())
    source = gw.group("asr")[0]["model_info"]["id"]

    for attempt in (
        lambda: service.set_blocked(handle="admin", name="asr", blocked=True),
        lambda: service.delete(handle="admin", name="asr"),
        lambda: service.set_source_blocked(
            handle="admin", name="asr", source_id=source, blocked=True
        ),
        lambda: service.delete_source(handle="admin", name="asr", source_id=source),
    ):
        with pytest.raises(BadRequestError) as refused:
            await attempt()
        assert "voice_asr_model" in str(refused.value)
    assert len(gw.writes()) == before

    await service.set_role(handle="admin", role="voice_asr_model", model=None)
    reset_cache()
    await service.set_blocked(handle="admin", name="asr", blocked=True)


_CHOICES = {
    "glm": {"label": "GLM", "tier": "included", "overseas": False},
    "gemini": {"label": "Gemini", "tier": "premium", "overseas": True},
    "kimi": {"label": "Kimi", "tier": "premium", "overseas": False},
}


def test_a_projects_own_subagent_default_still_wins():
    model_roles.install({"subagent_model": "gemini"})
    policy = gate.policy_of({}, None, overseas_approved=True)

    assert (
        subagent_default(
            {"default_subagent_model": "glm", "default_model": "glm"},
            _CHOICES,
            policy,
        )
        == "glm"
    )


def test_an_approved_team_gets_an_overseas_platform_subagent_default():
    model_roles.install({"subagent_model": "gemini"})
    policy = gate.policy_of({}, None, overseas_approved=True)

    assert subagent_default({"default_model": "glm"}, _CHOICES, policy) == "gemini"


def test_a_team_not_approved_for_overseas_models_keeps_its_main_default():
    """The same gate its own turns pass: it could not run the model."""
    model_roles.install({"subagent_model": "gemini"})
    policy = gate.policy_of({}, None, overseas_approved=False)

    assert subagent_default({"default_model": "glm"}, _CHOICES, policy) == "glm"


def test_a_plan_without_the_models_tier_keeps_its_main_default():
    model_roles.install({"subagent_model": "kimi"})
    policy = gate.policy_of({}, frozenset({"included"}), overseas_approved=False)

    assert subagent_default({"default_model": "glm"}, _CHOICES, policy) == "glm"
    # With no platform choice at all, as before: the project's main default.
    model_roles.reset()
    assert subagent_default({"default_model": "glm"}, _CHOICES, policy) == "glm"


@pytest.mark.anyio
async def test_a_role_runs_on_a_model_priced_on_one_side():
    """Speech to text is billed for audio in, nothing out; that is billable."""
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)
    await service.add(
        handle="admin",
        payload=ModelCreate(
            name="qwen-audio-asr",
            upstream_model="qwen-asr/flash",
            prices={"input": 1.2394e-6, "output": 0},
        ),
    )
    reset_cache()

    await service.set_role(
        handle="admin", role="voice_asr_model", model="qwen-audio-asr"
    )
    assert model_roles.get("voice_asr_model") == "qwen-audio-asr"


@pytest.mark.anyio
async def test_a_model_the_default_role_runs_on_cannot_be_unlisted():
    """Every project without its own default would be offered a model the
    picker no longer lists."""
    gw = _Gateway([_KIMI_CONFIG])
    service = _service(gw)
    await _models(service)
    await service.set_role(handle="admin", role="agent_model", model="glm")
    reset_cache()
    before = len(gw.writes())

    with pytest.raises(BadRequestError) as refused:
        await service.update(
            handle="admin", name="glm", payload=ModelUpdate(selectable=False)
        )
    assert "agent_model" in str(refused.value)
    assert len(gw.writes()) == before
