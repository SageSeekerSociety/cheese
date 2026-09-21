"""A forge that answers from a ledger instead of from a network.

The accept path's seam is `review/forge.py`: a provider that says what it can
do, merges, refreshes, polls and overrides. Everything on the other side of that
seam — GitHub's REST API, a campus GitLab, whatever comes third — is somebody
else's machine, so the only way to test "does the acceptance flow read the
forge's conclusion" is to hold a forge whose conclusion we set.

`LedgerForge` is that forge. Its conclusion is one of four words — green, red,
running, unknown — and it records every action the accept path asked of it, in
order, so a test can assert both what came back and what was called.

It is a double, not a mock of our own code: it implements the real `Forge` ABC,
so a capability bit or an operation added to the interface breaks it here rather
than leaving a test that passes against a shape nothing has any more. It is not
picked by `forge_for` — `serves()` is always False and a test hands the instance
over directly, because a real project's facts can never produce a forge whose
conclusion the test dictates.
"""

from __future__ import annotations

from typing import Literal, NoReturn

from app.core.errors import ValidationError
from app.domain.review import forge as forge_mod
from app.domain.review.models import AcceptStatus

#: 这个托管方对「能不能合」的结论。`unknown` = 我们读不到，不是「通过」。
Conclusion = Literal["green", "red", "running", "unknown"]

_STATE: dict[Conclusion, str] = {
    "green": "clean",
    "red": "blocked",
    "running": "blocked",
    "unknown": "unknown",
}
_DETAIL: dict[Conclusion, str] = {
    "green": "检查全绿",
    "red": "必跑检查红了",
    "running": "CI 还在跑",
    "unknown": "读不到这个托管方的结论",
}


class LedgerForge(forge_mod.Forge):
    """A forge whose conclusion a test dictates, and whose calls it can read."""

    kind = forge_mod.ForgeKind.forgejo

    @property
    def declaration(self) -> str:
        return "ℹ️ 假托管方"

    def __init__(
        self,
        capabilities: forge_mod.ForgeCapabilities,
        *,
        conclusion: Conclusion = "green",
    ) -> None:
        super().__init__(capabilities)
        self.conclusion: Conclusion = conclusion
        #: 采纳流程对这个托管方做过的每一个动作，按顺序。
        self.calls: list[tuple] = []

    @classmethod
    def serves(cls, capabilities: forge_mod.ForgeCapabilities) -> bool:
        # Never picked by `forge_for`; a test hands this instance over directly.
        return False

    def merge_state(self, head: str | None) -> dict:
        """What the platform would mirror onto the card from this conclusion."""
        return {
            "state": _STATE[self.conclusion],
            "who": "human",
            "reasons": [
                {"kind": "check", "checks": [], "detail": _DETAIL[self.conclusion]}
            ],
            "head_sha": head,
            "checked_at": None,
            "since": None,
        }

    async def accept(self, service, card, topic, decided_by, *, seen_head):
        self.calls.append(("accept", decided_by, seen_head))
        if self.conclusion != "green":
            # 没有结论不是通过（#465/#468）：还在跑的和红的一样拦住。
            raise ValidationError(f"托管方还不允许合并：{_DETAIL[self.conclusion]}")
        card.status = AcceptStatus.accepted
        return card

    async def refresh_unseen_head(self, service, card, topic, action) -> NoReturn:
        self.calls.append(("refresh", action))
        raise ValidationError("这个版本还没在页面上显示过")

    async def poll(self, service, card, topic, *, chat_service, runner) -> None:
        self.calls.append(("poll",))
        card.merge_state = self.merge_state(card.pr_head_sha)

    async def merge_despite_checks(
        self, service, card, topic, decided_by, *, seen_head, reason
    ):
        self.calls.append(("override", decided_by, seen_head, reason))
        card.status = AcceptStatus.accepted
        return card


def code_project_forge(**kwargs) -> LedgerForge:
    """一个托管提案页的假 forge —— 用户在 forge 那边，卡上放链接（结论 51）。"""
    return LedgerForge(
        forge_mod.ForgeCapabilities(
            reports_checks=True,
            hosts_proposals=True,
            can_write_remote=True,
            has_external_remote=True,
            pushes_to_external_remote=True,
            identity=forge_mod.ForgeIdentity.user,
        ),
        **kwargs,
    )


def doc_project_forge(**kwargs) -> LedgerForge:
    """一个不托管提案页的假 forge —— 用户不在 forge 那边，评审在房间和卡片里。"""
    return LedgerForge(
        forge_mod.ForgeCapabilities(
            reports_checks=False,
            hosts_proposals=False,
            can_write_remote=True,
            has_external_remote=True,
            pushes_to_external_remote=True,
            identity=forge_mod.ForgeIdentity.platform,
        ),
        **kwargs,
    )
