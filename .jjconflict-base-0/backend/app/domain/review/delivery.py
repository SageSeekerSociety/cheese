"""What a card's delivery actually consists of — the steps this project HAS.

The card used to draw a fixed four-step chain: 已采纳 → CI 检查 → 合并进 main →
部署. Every project got all four, because the platform assumed every project was
this one. Two of them were wrong for most projects:

- **部署** was assumed from a platform-wide setting naming ONE repo's workflow
  file. #206 removed it from the accept entirely (a per-project ops concept
  cannot be a platform state), and with it the reason to draw the step.
- **CI 检查** is not universal either. A project with no GitHub binding has no
  external checks at all — which #363 calls a legitimate shape, "a repo with no
  CI configured, where accepting is a purely human decision", not a degradation.
  Drawing a check step there promises something that will never happen.

So the steps are built from what the forge actually offers, and a project sees
its own chain. The rule is: **show only what the platform can be sure of.** A
step that might exist is worse than a step that is missing, because a chain is
read as a promise of what comes next.

Deployment is deliberately absent even now that `deploy-dev.yml` names its
environment (so GitHub records a real deployment for each dev deploy, queryable
by sha instead of guessed by filename). Two reasons, both about honesty rather
than effort: nothing observes a card after the merge any more — #206 ended the
polling at that point — so a deploy step would be drawn with no mechanism behind
it; and where a deployment IS observed belongs with the ops room (#190), which
owns "did it reach the box" for every project, not just the ones on GitHub.
"""

from dataclasses import asdict, dataclass
from typing import Literal

from app.domain.review.forge import Forge
from app.domain.review.models import AcceptCard, AcceptStatus

StepState = Literal["done", "active", "todo"]


@dataclass(frozen=True)
class DeliveryStep:
    key: str
    label: str
    state: StepState


#: Statuses in which the human has decided and the machine is carrying it out.
#: Outside these the card is not "delivering" — it is waiting on a person, or
#: finished, or dead — and the chain says nothing.
_IN_FLIGHT = {AcceptStatus.pr_open}


def steps_for(card: AcceptCard, forge: Forge) -> list[dict]:
    """The delivery chain for this card, or an empty list.

    Empty is a real answer and the common one: a card waiting on a reviewer has
    no machine work in flight to describe, and a settled card's chain is over.
    """
    if card.status not in _IN_FLIGHT:
        return []

    steps = [DeliveryStep("accepted", "已采纳", "done")]
    if forge.has_external_checks:
        # `pr_open` means exactly this: waiting on the forge's own checks. The
        # merge follows automatically when they go green.
        steps.append(DeliveryStep("checks", "检查", "active"))
        steps.append(DeliveryStep("merge", "合并进 main", "todo"))
    else:
        # No external checks: the platform is the forge and the merge is the
        # only remaining act, so it is what is happening right now.
        steps.append(DeliveryStep("merge", "合并进 main", "active"))
    return [asdict(step) for step in steps]
