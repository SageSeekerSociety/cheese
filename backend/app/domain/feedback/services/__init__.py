"""Feedback rules.

Everything a route must not be trusted to remember lives here:

* **who can see a row** — one predicate, used by every read path, so a new
  endpoint cannot accidentally get it wrong (§4.3 of the design note).
* **status and timeline move together** — one method does both, in the caller's
  transaction. There is no other way to write `status`, which is what keeps the
  timeline from becoming a partial record of what happened.
* **comments are two levels deep** — the fold-up rule, copied from the prototype.

Three product decisions the user had not ruled on are taken here as defaults,
each in one place, each revertible without touching a route:

1. **Who is an admin** — `settings.feedback_triage_handles`, a roster of its
   own from deployment config. Not the platform admins (`app/domain/admin/`):
   they run the admin screens for other jobs, and private feedback is not
   theirs to read by virtue of that.
2. **`security` is a subtype of `private`, not a second axis.** A security report
   is invisible to non-admins exactly as a private one is; the flag only routes
   it into the admin's security tab. So `security=True` narrows visibility, and
   the narrowing is applied **at read time** in `may_see` — not by overwriting
   `visibility` when an admin sets the flag. Two reasons it goes that way:
   `visibility` is the reporter's own choice and rewriting it would edit their
   decision behind their back, and deciding who may see a row is a policy
   question, which the repository deliberately does not answer.
3. **Resolved items sink, but only bugs.** See `_visible_tab` in repositories —
   a resolved suggestion stays in the list. Hiding suggestions is the wider
   reading and much harder to notice going wrong; this is the narrow one.

**This package is still the only entry point to those rules.** It was one
1178-line module until 2026-10-15; the method bodies moved into the sibling
modules — `visibility` (who can see / delete / claim), `reads` (lists, counts,
series, detail), `writes` (create, status, claim) and `engagement` (support,
comments, delete) — and `vocabulary` holds the closed word lists the routes and
the frontend controls share. Every method on `FeedbackService` is a one-line
delegation to the module function of the same name, so the import path, the
public surface and the patch points (`app/api/routes/`, `platform_stats`,
`shipping`, and the unit tests that monkeypatch by name) are all unchanged.

The split follows 「一个决定只写一遍」: each predicate, each read and each write
has exactly one implementation, in the module named above, and the class only
forwards. A forwarding method takes no decision — that is what keeps the two
copies from drifting apart, which is the failure the docstrings above are about.

拆成**包**而不是几个同级模块，是为了不欠新债：同级的 `services` 与 `reads`
互相 import 会在 C3（`sibling domains are acyclic`）上多出四条环，而包里的子
模块 import 的是父包 —— 那条「子指向祖先」的边契约本来就不算环。形状和
`app/domain/review/services/` 一样。
"""

from __future__ import annotations

# 兼容门面：`app.domain.feedback.services` 仍是路由、`platform_stats`、`shipping`
# 和单测的导入路径。拆包之前那份 services.py 的模块名字空间里只有这一组名字
# （`FeedbackService` 与四个封闭词表），所以这里也只再导出这一组——每个都写成
# `X as X`，`FeedbackService` 仍是同一个类对象，`repo` 这个短名也照旧。
from app.domain.feedback import repositories as repo  # noqa: F401
from app.domain.feedback.services.service import FeedbackService as FeedbackService
from app.domain.feedback.services.vocabulary import (
    ADMIN_TABS as ADMIN_TABS,
)
from app.domain.feedback.services.vocabulary import (
    PUBLIC_TABS as PUBLIC_TABS,
)
from app.domain.feedback.services.vocabulary import (
    SORTS as SORTS,
)
from app.domain.feedback.services.vocabulary import (
    STATUS_LADDER as STATUS_LADDER,
)
