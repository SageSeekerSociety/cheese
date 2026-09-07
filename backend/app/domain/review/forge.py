"""Which forge a project's accept goes through.

采纳 = merging the topic branch into the project's authoritative main, wherever
that main lives (#363). Where it lives is the forge, and the forge decides three
things the accept path used to work out by crossing booleans at the call site:

- whether accepting means merging a PR,
- whether a failure on that path may fall back to a local merge or must stop,
- whether anything outside the platform runs checks on the change.

Those booleans (`pr_publish.enabled()` × `_github_bound()` × `card.pr_number`)
produced three lanes with different failure rules, and getting the crossing
wrong is not hypothetical: #362 is a GitHub-bound project whose accept fell into
the local merge and pushed straight to main with no PR and no CI, twice in one
day. The crossing now happens once, here, with a name on each outcome.

This module deliberately does NOT own the operations. Opening, merging and
pushing stay in `AcceptService`, where the session, the workspace and the
notification plumbing already are; moving ~600 lines of the most incident-prone
path in the same change as re-deciding its shape is how a refactor becomes an
outage. What a future forge (GitLab, Gitea, a self-hosted host) has to do is
therefore: add a `ForgeKind`, teach `resolve()` when to pick it, and give
`AcceptService` the operations for it — the dispatch is already a value, not a
chain of `if`s.
"""

import enum
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.errors import ValidationError

#: What the card says when the platform's own repo IS the forge, so an accept
#: with no PR can never be mistaken for a bound project that skipped one.
PLATFORM_FORGE_NOTE = (
    "ℹ️ 本项目未接 GitHub：采纳即合并进平台仓库的 main（无 PR、无外部 CI）"
)

#: Raised as the accept's error when the binding cannot be determined at all.
BINDING_UNKNOWN_MESSAGE = "采纳未完成：暂时无法判定项目的 GitHub 绑定状态，稍后重试采纳"


class ForgeKind(enum.StrEnum):
    #: The platform's GitHub App owns PR creation and merging (#296). The
    #: project has an installation AND a GitHub https upstream.
    github_app = "github_app"
    #: No GitHub at all: the platform's bare repo is the authoritative main and
    #: the local merge is this project's one legitimate accept (#363) — not a
    #: degrade, which is why it carries a note saying so.
    platform = "platform"


@dataclass(frozen=True)
class Forge:
    kind: ForgeKind

    @property
    def requires_pr(self) -> bool:
        """A PR is the ONLY way in, and any failure on that path stops the
        accept rather than falling back. This is the #362 rule: for a bound
        project, "the PR did not work out" must never quietly become "pushed to
        main directly"."""
        return self.kind is ForgeKind.github_app

    @property
    def has_external_checks(self) -> bool:
        """Does something outside the platform run checks here?

        False is not a degradation — an unlinked project is a repo with no CI
        configured, where accepting is a purely human decision (#363). It is
        what lets the delivery surface show the steps a project actually has
        instead of a fixed chain ending in one it does not.
        """
        return self.kind is not ForgeKind.platform

    @property
    def note(self) -> str:
        """What the card should say about this lane, if anything."""
        return PLATFORM_FORGE_NOTE if self.kind is ForgeKind.platform else ""


async def resolve(
    *,
    project_id: uuid.UUID,
    is_github_bound: Callable[[uuid.UUID], Awaitable[bool]],
) -> Forge:
    """Pick the forge for this project's accept.

    ``is_github_bound`` is injected so the decision is testable without a
    database, a GitHub App or a workspace — the three things that made the old
    inline crossing effectively untestable. A deployment with no App
    configured answers "not bound" for every project, so it lands on the
    platform forge (#718 deleted the personal-token lane that used to catch
    that case).

    Fails CLOSED: if the binding cannot be determined, this raises rather than
    guessing, because the wrong guess is the one that pushes to main.
    """
    try:
        bound = await is_github_bound(project_id)
    except Exception as exc:  # noqa: BLE001 — cannot pick a lane blind
        raise ValidationError(BINDING_UNKNOWN_MESSAGE) from exc
    return Forge(ForgeKind.github_app if bound else ForgeKind.platform)
