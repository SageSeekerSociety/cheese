"""Where a person's 芝士 compacts its conversation: by the model it runs on,
the way sessions on Claude Code do, not at one size for every model."""

from types import SimpleNamespace

from app.domain.agent import gateway_catalog
from app.domain.agent.personal import session as personal


def _catalog(monkeypatch, *models):
    monkeypatch.setattr(gateway_catalog, "snapshot", lambda: list(models))


def _model(id, window=None, compact=None):
    return SimpleNamespace(id=id, context_window=window, compact_window=compact)


def test_it_compacts_where_the_provider_recommends(monkeypatch):
    _catalog(monkeypatch, _model("deepseek-flash", 920_000, 786_432))

    told = personal.context_tokens("deepseek-flash")

    assert told - personal.RESERVE_TOKENS == 786_432


def test_without_a_recommendation_it_compacts_short_of_the_window(monkeypatch):
    _catalog(monkeypatch, _model("glm-5.2", 128_000))

    assert personal.context_tokens("glm-5.2") == 128_000


def test_a_recommendation_past_the_window_is_held_to_it(monkeypatch):
    _catalog(monkeypatch, _model("small", 64_000, 64_000))

    assert personal.context_tokens("small") == 64_000


def test_a_model_the_gateway_says_nothing_of_gets_the_assumed_window(monkeypatch):
    _catalog(monkeypatch, _model("glm-5.2"))

    assert personal.context_tokens("glm-5.2") == personal.ASSUMED_WINDOW
    assert personal.context_tokens("unlisted") == personal.ASSUMED_WINDOW
