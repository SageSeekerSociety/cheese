"""功能数据的注册表:one entry per feature that has a page.

Adding a page is one entry here plus the module it points at. The entry carries
what the catalogue needs (id, title, one line) and what the report endpoint
needs (``load``); it does **not** carry a route or a component path, because the
front end owns its own routes and deciding here which Vue file renders a feature
would make this module a second copy of ``router/feedback.ts`` that can drift.

``title`` and ``summary`` are Chinese strings, and the front end prefers its own
translated keys for them: the pair here is the catalogue's fallback for a
feature the front end has no page for yet (the response is also what an agent or
a script reads), not the text a Chinese-reading admin sees. The manual has the
full procedure, including why the two lists have to be kept in step.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.domain.feature_stats.features import docs_assistant, task_naming

# ``load`` is the feature's own report for one window; its return shape is the
# feature's business (see ``features/docs_assistant``).
Loader = Callable[..., Awaitable[dict]]


@dataclass(frozen=True)
class Feature:
    id: str
    title: str
    summary: str
    load: Loader


FEATURES: tuple[Feature, ...] = (
    Feature(
        id=docs_assistant.FEATURE_ID,
        title=docs_assistant.TITLE,
        summary=docs_assistant.SUMMARY,
        load=docs_assistant.load,
    ),
    Feature(
        id=task_naming.FEATURE_ID,
        title=task_naming.TITLE,
        summary=task_naming.SUMMARY,
        load=task_naming.load,
    ),
)

_BY_ID = {feature.id: feature for feature in FEATURES}


def find(feature_id: str) -> Feature | None:
    """The feature with this id, or None — the route turns that into a 404."""
    return _BY_ID.get(feature_id)


def catalogue() -> list[dict[str, str]]:
    """The catalogue, in registration order: it is a list of destinations, and
    the order is the only thing the front end has to agree with."""
    return [
        {"id": feature.id, "title": feature.title, "summary": feature.summary}
        for feature in FEATURES
    ]
