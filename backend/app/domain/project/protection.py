"""Branch protection policy (issue #718): 平台侧的分支保护规则.

「只在绿的时候合」要么平台自己执行，要么不存在——GitHub 对 free 计划私有仓什么都
拦不住。规则照 GitHub 分支保护那一页配置，按项目存在 ``Project.settings`` 的
``branch_protection`` 键下（自由 JSON，无独立表）；GitHub 自己能判定的听 GitHub，
判定不了的平台按这份配置补位。

存储形状（每个键只在被明确配置过时才存在，缺省语义住在读取侧）::

    settings["branch_protection"] = {
        "required_checks": [{"name": "test", "paths": ["backend/**"]}, ...],
        "strict": bool,
        "dismiss_stale": bool,
        "auto_merge_allowed": bool,
        "override_handles": ["alice", ...],
        "default_reviewer": "bob",
    }

``approvals_required`` 是个例外：它早于本模块存在，留在
``settings["approvals_required"]`` 原地不动（不搬家、不双写）——
:func:`branch_protection_of` 把它一并读进来，让策略有一个完整的读取口。
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

import httpx

from app.domain.project.models import Project

BRANCH_PROTECTION_KEY = "branch_protection"

# Write-side caps: settings is a JSON column loaded on every project read, so a
# runaway list is a per-request tax, not just an ugly row.
MAX_REQUIRED_CHECKS = 50
MAX_PATHS_PER_CHECK = 50
MAX_CHECK_NAME_CHARS = 200
MAX_PATH_CHARS = 512
MAX_OVERRIDE_HANDLES = 100
MAX_HANDLE_CHARS = 64


@dataclass(frozen=True)
class RequiredCheck:
    """One named check a PR must pass before merging.

    ``paths`` scopes it (fnmatch globs against the changed files; e.g.
    ``backend/**``): empty = required on every PR.
    """

    name: str
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class BranchProtection:
    """A project's effective branch-protection policy, defaults applied.

    Defaults follow GitHub's branch-protection page — everything off — with one
    deliberate inversion: ``dismiss_stale`` is ON. GitHub defaults it off
    because it assumes the pusher is a trusted human; here the pusher is 芝士
    holding App write credentials, so a new commit voids existing accepts
    unless the project explicitly opts out.

    ``override_handles`` is ``None`` when unconfigured, meaning the default
    set: the project's owner and leads (a roster lookup, so not resolvable
    from the project row alone).
    """

    required_checks: tuple[RequiredCheck, ...] = ()
    strict: bool = False
    dismiss_stale: bool = True
    auto_merge_allowed: bool = False
    override_handles: tuple[str, ...] | None = None
    approvals_required: int = 1
    default_reviewer: str = ""


def _approvals_required(settings: dict) -> int:
    """``settings["approvals_required"]``, default 1 — the accepter's own
    accept counts, so unconfigured projects are unchanged."""
    try:
        return max(1, int(settings.get("approvals_required") or 1))
    except (TypeError, ValueError):
        return 1


def branch_protection_of(project: Project | None) -> BranchProtection:
    """The project's branch-protection policy, read defensively.

    A malformed stored value never raises — each field falls back to its
    default independently, because a policy read happens on the accept path
    where a 500 would block every merge over one bad settings write.
    """
    settings = (project.settings or {}) if project is not None else {}
    raw = settings.get(BRANCH_PROTECTION_KEY)
    if not isinstance(raw, dict):
        raw = {}

    checks: list[RequiredCheck] = []
    raw_checks = raw.get("required_checks")
    if isinstance(raw_checks, list):
        for item in raw_checks:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            paths = item.get("paths")
            path_tuple = (
                tuple(str(p) for p in paths if str(p).strip())
                if isinstance(paths, list)
                else ()
            )
            checks.append(RequiredCheck(name=name, paths=path_tuple))

    raw_overrides = raw.get("override_handles")
    overrides: tuple[str, ...] | None = None
    if isinstance(raw_overrides, list):
        overrides = tuple(str(h).strip() for h in raw_overrides if str(h).strip())

    reviewer = raw.get("default_reviewer")
    if not isinstance(reviewer, str):
        reviewer = ""

    return BranchProtection(
        required_checks=tuple(checks),
        strict=bool(raw.get("strict", False)),
        dismiss_stale=bool(raw.get("dismiss_stale", True)),
        auto_merge_allowed=bool(raw.get("auto_merge_allowed", False)),
        override_handles=overrides,
        approvals_required=_approvals_required(settings),
        default_reviewer=reviewer.strip(),
    )


# --- Write side -----------------------------------------------------------


def _validate_glob(path: object) -> str:
    text = str(path or "").strip()
    if not text:
        raise ValueError("检查的路径范围不能是空串")
    if len(text) > MAX_PATH_CHARS:
        raise ValueError(f"路径范围不能超过 {MAX_PATH_CHARS} 个字符")
    if "\x00" in text or "\n" in text:
        raise ValueError("路径范围不能包含 NUL 或换行")
    if text.startswith("/"):
        raise ValueError(f"路径范围要相对仓库根，不能以 / 开头：{text!r}")
    try:
        re.compile(fnmatch.translate(text))
    except re.error as e:  # pragma: no cover — translate rarely yields bad re
        raise ValueError(f"路径范围不是合法 glob：{text!r}（{e}）") from None
    return text


def _validate_required_checks(value: object) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError("required_checks 必须是列表")
    if len(value) > MAX_REQUIRED_CHECKS:
        raise ValueError(f"必跑检查最多 {MAX_REQUIRED_CHECKS} 条")
    checks: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("每条必跑检查要写成 {name, paths?}")
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError("检查名不能为空")
        if len(name) > MAX_CHECK_NAME_CHARS:
            raise ValueError(f"检查名不能超过 {MAX_CHECK_NAME_CHARS} 个字符")
        if "\x00" in name or "\n" in name:
            raise ValueError("检查名不能包含 NUL 或换行")
        raw_paths = item.get("paths")
        if raw_paths in (None, []):
            checks.append({"name": name})
            continue
        if not isinstance(raw_paths, list):
            raise ValueError(f"检查 {name!r} 的 paths 必须是列表")
        if len(raw_paths) > MAX_PATHS_PER_CHECK:
            raise ValueError(f"一条检查的路径范围最多 {MAX_PATHS_PER_CHECK} 个")
        checks.append({"name": name, "paths": [_validate_glob(p) for p in raw_paths]})
    return checks


def _validate_handles(value: object, *, what: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{what} 必须是列表")
    if len(value) > MAX_OVERRIDE_HANDLES:
        raise ValueError(f"{what} 最多 {MAX_OVERRIDE_HANDLES} 人")
    handles: list[str] = []
    for h in value:
        handle = str(h or "").strip()
        if not handle:
            raise ValueError(f"{what} 里有空的 handle")
        if len(handle) > MAX_HANDLE_CHARS:
            raise ValueError(f"handle 不能超过 {MAX_HANDLE_CHARS} 个字符")
        if handle not in handles:
            handles.append(handle)
    return handles


def _require_bool(value: object, *, key: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{key} 必须是 true/false")
    return value


def apply_branch_protection_update(stored: object, body: dict) -> dict:
    """Partial-update the stored ``branch_protection`` dict from a PUT body.

    Only the keys present in ``body`` change (partial-update 语义)。
    ``override_handles: null`` 和空的 ``default_reviewer`` 把键删掉——回到缺省，
    而不是存一个「显式空」。Raises ``ValueError`` (message is user-facing).
    """
    new = dict(stored) if isinstance(stored, dict) else {}
    if "required_checks" in body:
        checks = _validate_required_checks(body["required_checks"])
        if checks:
            new["required_checks"] = checks
        else:
            new.pop("required_checks", None)
    for key in ("strict", "dismiss_stale", "auto_merge_allowed"):
        if key in body:
            new[key] = _require_bool(body[key], key=key)
    if "override_handles" in body:
        if body["override_handles"] is None:
            new.pop("override_handles", None)  # back to owner + leads
        else:
            new["override_handles"] = _validate_handles(
                body["override_handles"], what="人工放行名单"
            )
    if "default_reviewer" in body:
        reviewer = str(body["default_reviewer"] or "").strip()
        if len(reviewer) > MAX_HANDLE_CHARS:
            raise ValueError(f"handle 不能超过 {MAX_HANDLE_CHARS} 个字符")
        if "\x00" in reviewer:
            raise ValueError("default_reviewer 不能包含 NUL 字节")
        if reviewer:
            new["default_reviewer"] = reviewer
        else:
            new.pop("default_reviewer", None)
    return new


# --- What GitHub itself is doing (read-only, always degrades) -------------


@dataclass(frozen=True)
class GitHubProtection:
    """Whether GitHub is enforcing protection on the bound repo's trunk.

    ``status``: ``unbound`` (no GitHub repo bound), ``enforced`` (GitHub is
    running rules of its own — the settings page greys out the platform's
    same-name rules), ``none`` (asked, definitively unprotected), ``unknown``
    (403 / network / bad credentials — this repo's own free-plan private repo
    answers 403 to every protection endpoint, so unknown is an everyday
    answer, not an error).
    """

    status: str
    detail: str = ""

    @property
    def enforced(self) -> bool:
        return self.status == "enforced"


GITHUB_UNBOUND = GitHubProtection("unbound", "项目没有绑定 GitHub 仓库")


async def github_repo_snapshot(
    repo: str,
    token: str | None,
    *,
    api_base: str = "https://api.github.com",
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, GitHubProtection]:
    """(merge_method, protection) for a bound repo, read with the App's token.

    Display-only, so every failure degrades instead of raising: 403 (free-plan
    private repos 403 every protection endpoint), 404, bad token and network
    trouble all come back as a valid answer — never an exception, so the
    settings page never 500s over GitHub's mood.
    """
    merge_method = "squash"
    if not token:
        return merge_method, GitHubProtection("unknown", "拿不到 GitHub 凭据")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=10.0, headers=headers
        ) as client:
            r = await client.get(f"{api_base}/repos/{repo}")
            if r.status_code != 200:
                return merge_method, GitHubProtection(
                    "unknown", f"读不到仓库设置（HTTP {r.status_code}）"
                )
            info = r.json()
            branch = str(info.get("default_branch") or "main")
            if info.get("allow_squash_merge", True):
                merge_method = "squash"
            elif info.get("allow_merge_commit"):
                merge_method = "merge"
            elif info.get("allow_rebase_merge"):
                merge_method = "rebase"

            prot = await client.get(
                f"{api_base}/repos/{repo}/branches/{branch}/protection"
            )
            if prot.status_code == 200:
                return merge_method, GitHubProtection(
                    "enforced", f"GitHub 已在保护 {branch} 分支"
                )
            rules = await client.get(f"{api_base}/repos/{repo}/rules/branches/{branch}")
            if rules.status_code == 200:
                body = rules.json()
                if isinstance(body, list) and body:
                    return merge_method, GitHubProtection(
                        "enforced", f"GitHub rulesets 正在保护 {branch} 分支"
                    )
            # No positive signal. 404s are a definitive "nothing is on"; a 403
            # anywhere means GitHub refused to say (free plan / token scope).
            if prot.status_code == 404 and rules.status_code in (200, 404):
                return merge_method, GitHubProtection("none", "GitHub 侧未开启分支保护")
            return merge_method, GitHubProtection(
                "unknown",
                f"GitHub 没有回答（protection HTTP {prot.status_code}，"
                f"rules HTTP {rules.status_code}）",
            )
    except Exception as e:  # noqa: BLE001 — display-only: degrade, never 500
        return merge_method, GitHubProtection("unknown", f"查询 GitHub 失败：{e}")
