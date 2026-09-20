"""机构协议 (spec §4.2) — what a 赛题 offers and what it requires in return.

An institution publishes a 项目集/活动 (`space_categories`: 创研课 2026 秋,
黑客松第三期) holding 赛题 (`task`). Linking a project to a 赛题 accepts a
protocol: the institution provides a 资源包 (compute credits, a default expert
role), the project accepts 条件 (e.g. 结题答辩 must be accepted by a mentor).

**Where the protocol lives (#370, decided 2026-08-17).** On the 项目集, with an
optional per-赛题 override — option (c). The reason is the real usage: 创研课 has
twenty 赛题 and one set of terms, so a teacher configures it once. Putting it
only on the 赛题 would mean twenty copies to keep in step; putting it only on the
项目集 would leave no way to say "this one 赛题 gets more compute". The override
is a whole-key replacement, not a deep merge — a half-inherited resource pack is
harder to reason about than either source alone.

This replaces the cheesex `task_templates` table, which held these same four
fields for a parallel 题目 hierarchy nobody could create from the UI. The 知是
side already had the levels; it was missing only the protocol.
"""

from dataclasses import dataclass, field
from typing import Any

#: Keys a 赛题 may override. Anything else on the category is inherited as-is.
#: `shell` rides the same chain on purpose: whether a 项目集 is a 创研课 or an
#: 办公 workspace is the same kind of statement as what it provides, and it
#: needs the same "configure it once, override this one 赛题" behaviour. See
#: `app.domain.shell` for what the value means; here it is only a name.
_OVERRIDABLE = ("resource_pack", "conditions", "default_role", "shell")


@dataclass(frozen=True)
class Protocol:
    """The effective terms for one 赛题."""

    #: What the institution provides. `{"compute_credits": N}` issues a grant.
    resource_pack: dict[str, Any] = field(default_factory=dict)
    #: What the project accepts, e.g.
    #: ``{"required_topic": "结题答辩", "reviewer_role": "mentor"}``.
    conditions: list[dict[str, Any]] = field(default_factory=list)
    #: Expert role a project inherits when it has none of its own (spec §8.2).
    default_role: str | None = None
    #: Which 壳 the projects under this protocol run: a NAME, resolved against
    #: `app.domain.shell.catalog.CATALOG`. The declaration is platform-owned and
    #: the institution only picks one, so a 项目集 cannot grow a private 壳 that
    #: no other 项目集 can use — and cannot ship code, which is the same rule
    #: (`app.domain.shell`). None = nobody said, so `default` is in force.
    shell: str | None = None

    @property
    def compute_credits(self) -> float:
        """Credits to grant, or 0. Bools are rejected on purpose — `True` is an
        int in Python and would silently become a one-credit grant."""
        value = self.resource_pack.get("compute_credits")
        if isinstance(value, bool) or not isinstance(value, int | float):
            return 0.0
        return float(value) if value > 0 else 0.0

    def mentor_required_for(self, topic_title: str) -> bool:
        """Does a condition demand a mentor accept a topic with this title?

        A condition with an EMPTY ``required_topic`` matches nothing. It used to
        match everything, which turned one blank field into "every topic in this
        project needs a mentor" — a footgun a teacher cannot see from the form.
        """
        return any(
            c.get("reviewer_role") == "mentor"
            and (c.get("required_topic") or "").strip()
            and c["required_topic"].strip() in topic_title
            for c in self.conditions
        )


def resolve(*, category: Any | None, task: Any | None) -> Protocol:
    """The terms in force for ``task``: its 项目集's, with its own overrides.

    Both arguments are optional so callers do not have to branch: a 赛题 with no
    category, or a project with no 赛题 at all, resolves to an empty protocol —
    which is the 项目自治 default (spec §4), not an error.
    """
    values: dict[str, Any] = {
        "resource_pack": dict(getattr(category, "resource_pack", None) or {}),
        "conditions": list(getattr(category, "conditions", None) or []),
        "default_role": getattr(category, "default_role", None),
        "shell": getattr(category, "shell", None),
    }
    override = getattr(task, "protocol_override", None) or {}
    for key in _OVERRIDABLE:
        if key in override and override[key] is not None:
            values[key] = override[key]
    return Protocol(
        resource_pack=dict(values["resource_pack"] or {}),
        conditions=list(values["conditions"] or []),
        default_role=values["default_role"] or None,
        shell=values["shell"] or None,
    )
