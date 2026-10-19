"""The names one repository gives its own gate on the default branch.

Gating main in batches is a repository's design, not the platform's: a check
that aggregates the whole batch, a ref that marks where the last green batch
ended, and a workflow that can rerun named tests on one commit. A repository
that checks each merge on its own has none of the three, and one that has them
may spell them differently.

So a project has them only when it says so, under `main_gate` in its
`Project.settings`. The one exception is the repository that hosts the
platform itself (`settings.platform_repository`): its `required-ci.yml` and
`culprit-bisect.yml` are the platform's own, so it gets their names without
configuring anything. Every other project gets nothing — an empty gate, which
every read treats as "this repository has no such thing" — and the landing
watch falls back to judging each merge on its own, with no probe dispatched.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as platform
from app.domain.project.models import Project

#: Where `Project.settings` keeps the repository's own gate names.
MAIN_GATE_KEY = "main_gate"


@dataclass(frozen=True, slots=True)
class MainGate:
    """One repository's batched-main-gate names; empty = it has none."""

    #: The check run the batch's gate aggregates under, if the repository
    #: batches main at all.
    check: str = ""
    #: The ref marking where the last green batch ended.
    green_ref: str = ""
    #: The workflow that reruns named tests on one commit.
    probe_workflow: str = ""


#: The names the platform's own repository uses (`required-ci.yml`, #4249).
PLATFORM_GATE_NAMES = MainGate(
    check="CI required",
    green_ref="main-green",
    probe_workflow="culprit-bisect.yml",
)


def gate_for(project_settings: dict | None, *, repo: str) -> MainGate:
    """What this repository calls the pieces of its gate, or none of them.

    A project that configured `main_gate` is taken at its word — a key it left
    out is a name it did not choose, not one the platform fills in. The
    repository that hosts the platform gets the platform's own names; every
    other repository gets an empty gate, because the platform knows nothing
    about its CI.
    """
    raw = (project_settings or {}).get(MAIN_GATE_KEY)
    if isinstance(raw, dict):
        return MainGate(
            check=_text(raw.get("check")),
            green_ref=_text(raw.get("green_ref")),
            probe_workflow=_text(raw.get("probe_workflow")),
        )
    if repo.casefold() == platform.platform_repository.casefold():
        return PLATFORM_GATE_NAMES
    return MainGate()


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


async def main_gate_for(
    project_id: uuid.UUID, session: AsyncSession, *, repo: str
) -> MainGate:
    project_settings = await session.scalar(
        select(Project.settings).where(Project.id == project_id)
    )
    return gate_for(project_settings, repo=repo)
