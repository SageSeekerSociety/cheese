"""The one hard constraint that has to exist before the format gets used.

**An ops PR may only touch `ops/requests/`.**

Without it, a PR that reads like "just a deploy request" can carry a code change
in the same diff — and the human approving the operation would be authorizing
that code too, with their own identity. Once the manifest format is in use it is
too late to add this rule, so it ships with the format even though nothing
executes yet.

The rule is deliberately one-directional: a PR that touches NO manifest is an
ordinary PR and this module has nothing to say about it. That is also why the
manifest test is `ops/requests/*.yaml` and not "anything under ops/" — the
directory's own README/.gitkeep are repo furniture, not requests, so adding them
(as the PR that introduces this feature does) is not an ops PR.
"""

from collections.abc import Sequence
from pathlib import PurePosixPath

from app.domain.ops.manifest import REQUESTS_DIR

_PREFIX = f"{REQUESTS_DIR}/"


def is_manifest_path(path: str) -> bool:
    posix = PurePosixPath(path)
    return path.startswith(_PREFIX) and posix.suffix == ".yaml"


def is_ops_pr(changed_paths: Sequence[str]) -> bool:
    return any(is_manifest_path(p) for p in changed_paths)


def guard_issues(changed_paths: Sequence[str]) -> list[str]:
    """Problems with this PR's file list. Empty list = allowed."""
    if not is_ops_pr(changed_paths):
        return []
    issues: list[str] = []
    for path in changed_paths:
        if not is_manifest_path(path):
            issues.append(
                f"{path}: 操作请求 PR 只能碰 {REQUESTS_DIR}/ 下的清单文件。"
                "把代码改动拆成单独的 PR——否则批准这次操作的人，"
                "等于用自己的身份连带批准了这段代码。"
            )
            continue
        if PurePosixPath(path).parent.as_posix() != REQUESTS_DIR:
            issues.append(f"{path}: 清单必须直接放在 {REQUESTS_DIR}/ 下，不分子目录")
    return issues


__all__ = ["guard_issues", "is_manifest_path", "is_ops_pr"]
