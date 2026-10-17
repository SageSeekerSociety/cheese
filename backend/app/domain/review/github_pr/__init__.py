"""GitHub PR clients for PR-based accept.

Two client shapes live under this path, as they did when it was one module:

- `GitHubPRClient` (capital PR) — bound to one repo + `GitHubAppTokens`
  (write mint for push/open, read-only mint for check runs). Used by
  `review/pr_publish.py` to open the card's PR fire-and-forget when the card
  is filed.
- `GitHubPrClient` (lowercase pr) Protocol + `HttpxGitHubPrClient` — the
  token-per-call client the accept click and `review/pr_poll.py` drive:
  PR status, raw check runs, compares, merge (with the head-sha guard,
  #718), update-branch.

**This package is that module's facade.** The bodies moved into the sibling
modules below on 2026-10-16, cut by what each one is about:

  * `_shared` — what both shapes agree on: the result types, the error each
    raises into, which conclusions count as red, the merge-queue calls both
    make;
  * `protocol` — `GitHubPrClient`, the contract above;
  * `httpx_client` — `HttpxGitHubPrClient`, the real one, and the process-wide
    client `default_client()` hands out;
  * `failure_logs` — what a failed job's log is read down to before it reaches
    a card: the excerpt and its run-up, the redaction, the tail window;
  * `legacy` — `GitHubPRClient`, the App-installation-token shape.

Every module-level name the old file had is re-exported here under the same
name, so an import that named `app.domain.review.github_pr` for one of them
still resolves. Two of them are replaced on this module object by tests —
`GitHubPRClient` (`tests/integration/test_accept_pr.py`) and `set_default_client`
— so they have to stay here. No submodule imports this facade back: that
runtime import is the cycle this shape exists to avoid.
"""

from app.domain.review.github_pr._shared import (
    _FAILED_CONCLUSIONS as _FAILED_CONCLUSIONS,
)
from app.domain.review.github_pr._shared import (
    _NO_VERDICT_CONCLUSIONS as _NO_VERDICT_CONCLUSIONS,
)
from app.domain.review.github_pr._shared import (
    CheckState as CheckState,
)
from app.domain.review.github_pr._shared import (
    GitHubPrError as GitHubPrError,
)
from app.domain.review.github_pr._shared import (
    MergeResult as MergeResult,
)
from app.domain.review.github_pr._shared import (
    PullRequest as PullRequest,
)
from app.domain.review.github_pr._shared import (
    _enqueue_if_required as _enqueue_if_required,
)
from app.domain.review.github_pr._shared import (
    _github_message as _github_message,
)
from app.domain.review.github_pr._shared import (
    _queue_pr as _queue_pr,
)
from app.domain.review.github_pr._shared import (
    _queue_request as _queue_request,
)
from app.domain.review.github_pr._shared import (
    _refused as _refused,
)
from app.domain.review.github_pr._shared import (
    logger as logger,
)
from app.domain.review.github_pr._shared import (
    pr_branch_name as pr_branch_name,
)
from app.domain.review.github_pr.failure_logs import (
    _ANSI_RE as _ANSI_RE,
)
from app.domain.review.github_pr.failure_logs import (
    _FAILURE_DETAIL_MAX_JOBS as _FAILURE_DETAIL_MAX_JOBS,
)
from app.domain.review.github_pr.failure_logs import (
    _LOG_CONTEXT_LINES as _LOG_CONTEXT_LINES,
)
from app.domain.review.github_pr.failure_logs import (
    _LOG_DOWNLOAD_MAX_BYTES as _LOG_DOWNLOAD_MAX_BYTES,
)
from app.domain.review.github_pr.failure_logs import (
    _LOG_EXCERPT_MAX_CHARS as _LOG_EXCERPT_MAX_CHARS,
)
from app.domain.review.github_pr.failure_logs import (
    _LOG_LINE_MAX_CHARS as _LOG_LINE_MAX_CHARS,
)
from app.domain.review.github_pr.failure_logs import (
    _LOG_OUTPUT_FIELD_MAX_CHARS as _LOG_OUTPUT_FIELD_MAX_CHARS,
)
from app.domain.review.github_pr.failure_logs import (
    _LOG_TAIL_BYTES as _LOG_TAIL_BYTES,
)
from app.domain.review.github_pr.failure_logs import (
    _LOG_TIMESTAMP_RE as _LOG_TIMESTAMP_RE,
)
from app.domain.review.github_pr.failure_logs import (
    _TOKEN_SHAPE_RE as _TOKEN_SHAPE_RE,
)
from app.domain.review.github_pr.failure_logs import (
    _clean_log_line as _clean_log_line,
)
from app.domain.review.github_pr.failure_logs import (
    _download_log_tail as _download_log_tail,
)
from app.domain.review.github_pr.failure_logs import (
    _error_excerpt as _error_excerpt,
)
from app.domain.review.github_pr.failure_logs import (
    _failure_headline as _failure_headline,
)
from app.domain.review.github_pr.failure_logs import (
    _redact as _redact,
)
from app.domain.review.github_pr.httpx_client import (
    _GITHUB_ACTIONS_APP_SLUG as _GITHUB_ACTIONS_APP_SLUG,
)
from app.domain.review.github_pr.httpx_client import (
    _ZERO_CHECKS_GRACE_SECONDS as _ZERO_CHECKS_GRACE_SECONDS,
)
from app.domain.review.github_pr.httpx_client import (
    HttpxGitHubPrClient as HttpxGitHubPrClient,
)
from app.domain.review.github_pr.httpx_client import (
    _is_pr_already_exists as _is_pr_already_exists,
)
from app.domain.review.github_pr.httpx_client import (
    _summarize_runs as _summarize_runs,
)
from app.domain.review.github_pr.httpx_client import (
    default_client as default_client,
)
from app.domain.review.github_pr.httpx_client import (
    set_default_client as set_default_client,
)
from app.domain.review.github_pr.legacy import (
    GitHubPRClient as GitHubPRClient,
)
from app.domain.review.github_pr.legacy import (
    GitHubPRError as GitHubPRError,
)
from app.domain.review.github_pr.legacy import (
    GitHubPRMergeBlocked as GitHubPRMergeBlocked,
)
from app.domain.review.github_pr.legacy import (
    GitHubPRRateLimited as GitHubPRRateLimited,
)
from app.domain.review.github_pr.legacy import (
    OpenedPR as OpenedPR,
)
from app.domain.review.github_pr.legacy import (
    _as_pr_error as _as_pr_error,
)
from app.domain.review.github_pr.protocol import (
    GitHubPrClient as GitHubPrClient,
)
