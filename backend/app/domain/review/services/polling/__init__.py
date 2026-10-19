"""The PR poller and snapshot refresh: advance/refresh one card, mirror
the merge state, the seen-head guard, credentials and task closing."""

from __future__ import annotations

# 兼容门面：`app.domain.review.services.polling` 拆成包之后，调用方
# （`service.py` 逐个 `polling.<名字>` 转发）与测试拿到的仍是同一个名字空间。
# 每个模块级名字都逐个再导出（`X as X`），函数对象一格没动。
from app.domain.review.services.polling.advance import (
    _advance_claimed as _advance_claimed,
)
from app.domain.review.services.polling.advance import (
    _advance_github_card as _advance_github_card,
)
from app.domain.review.services.polling.advance import (
    _mark_task_merged as _mark_task_merged,
)
from app.domain.review.services.polling.advance import (
    advance_pr_card as advance_pr_card,
)
from app.domain.review.services.polling.advance import (
    note_poll_crashed as note_poll_crashed,
)
from app.domain.review.services.polling.card_poll import (
    _poll_pr_card as _poll_pr_card,
)
from app.domain.review.services.polling.card_poll import (
    _update_behind_branch as _update_behind_branch,
)
from app.domain.review.services.polling.clients import (
    _app_credentials as _app_credentials,
)
from app.domain.review.services.polling.clients import (
    _app_pr_client as _app_pr_client,
)
from app.domain.review.services.polling.clients import (
    _pr_poll_credentials as _pr_poll_credentials,
)
from app.domain.review.services.polling.clients import (
    _remote as _remote,
)
from app.domain.review.services.polling.clients import (
    _status_client as _status_client,
)
from app.domain.review.services.polling.seen_head import (
    _refresh_github_unseen_head as _refresh_github_unseen_head,
)
from app.domain.review.services.polling.seen_head import (
    _refresh_never_shown_card as _refresh_never_shown_card,
)
from app.domain.review.services.polling.seen_head import (
    _refresh_stale_card as _refresh_stale_card,
)
from app.domain.review.services.polling.seen_head import (
    _seen_head as _seen_head,
)
from app.domain.review.services.polling.seen_head import (
    _seen_head_or_refresh as _seen_head_or_refresh,
)
from app.domain.review.services.polling.snapshot import (
    _merge_snapshot_age_s as _merge_snapshot_age_s,
)
from app.domain.review.services.polling.snapshot import (
    _refresh_github_snapshot as _refresh_github_snapshot,
)
from app.domain.review.services.polling.snapshot import (
    _refresh_in_own_session as _refresh_in_own_session,
)
from app.domain.review.services.polling.snapshot import (
    _refresh_once as _refresh_once,
)
from app.domain.review.services.polling.snapshot import (
    _refresh_stale_pr_snapshot as _refresh_stale_pr_snapshot,
)
from app.domain.review.services.polling.snapshot import (
    _refreshing as _refreshing,
)
from app.domain.review.services.polling.snapshot import (
    _snapshot_is_due as _snapshot_is_due,
)
from app.domain.review.services.polling.snapshot import (
    refresh_stale_pr_snapshots as refresh_stale_pr_snapshots,
)
