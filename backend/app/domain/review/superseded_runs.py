"""被顶替的 workflow 运行 —— 同一个提交上，同一个 workflow 只认最新那次。

GitHub 的合并框读的是「这个提交上、这个 workflow 最新那次运行」的结论。旧运行
不会消失：草稿档那次被正式那次顶掉（`required-ci.yml` 的 concurrency
cancel-in-progress）、push 之后又跑了一次，旧运行留下的 check run 仍旧挂在同一个
sha 上，只是被 GitHub 取消了。平台按 `/commits/{sha}/check-runs` 一次把**全部**
check run 读回来，于是把被顶替的旧运行也算进「检查未通过」—— GitHub 说 clean 的
PR 在卡上显示红条。2026-10-12 的 PR #3463 就是这么一条：两条 cancelled 属于被顶替
的草稿档运行，而汇总 job 在旧运行里的名字还是没求值的 `${{ … }}` 表达式（新运行里
才求值成 `CI required`），所以「同名只认最新」收不掉它。

check run 里没有「我属于哪个 workflow」：它只带一个指向 Actions 作业页的 URL
（`/actions/runs/<run>/job/<job>`），`workflow_id` 只在 Actions 的运行列表里。所以
判「谁顶替了谁」要两步：先用 URL 认出每个 check run 属于哪次运行，再读一次运行
列表，把同一 workflow 里不是最新的那些运行挑出来。

读不到运行列表时原样返回（照旧报红）：这个模块只删自己认得出来的东西，一次读不到
不该把红抹成绿。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

import httpx

from app.core.forge_http import forge_client

#: `/actions/runs/<run>/job/<job>` — the only place a check-run names the
#: Actions run and job behind it.
_ACTIONS_JOB_URL_RE = re.compile(r"/actions/runs/(?P<run>\d+)/job/(?P<job>\d+)")

_PAGE_SIZE = 100
_MAX_PAGES = 5

#: 这些结论既不是「过了」，也不是「没通过」：收掉被顶替的运行之后剩下的照样要说
#: 出来（界面标「已取消」），不能读成绿。
_NOT_FINE_CONCLUSIONS = frozenset({"cancelled", "stale"})

#: 「绿」的那几个 —— 之外的已完成结论都值得多读一次运行列表（见
#: `needs_resolution`）。`skipped` 在这里算绿：它没有结论可丢，也不解释任何红。
_FINE_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})


def _job_url_match(run: dict) -> re.Match[str] | None:
    for key in ("html_url", "details_url"):
        match = _ACTIONS_JOB_URL_RE.search(str(run.get(key) or ""))
        if match:
            return match
    return None


def actions_job_id(run: dict) -> int | None:
    """The Actions job id behind a check-run, or None when it isn't one.

    Read out of the `/actions/runs/<run>/job/<job>` URL rather than the
    check-run's own `id`. The two are equal today (verified against this
    repo's check-runs on 2026-08-11), but only the URL shape *says* the
    check-run is an Actions job — other apps publish check-runs too (codecov
    and friends), and handing one of their ids to the jobs API just 404s.
    """
    match = _job_url_match(run)
    return int(match.group("job")) if match is not None else None


def actions_run_id(run: dict) -> int | None:
    """The Actions workflow run behind a check-run, or None when it isn't one."""
    match = _job_url_match(run)
    return int(match.group("run")) if match is not None else None


def needs_resolution(runs: Sequence[dict]) -> bool:
    """要不要多读一次运行列表。

    只有「跑完了、结论不绿」的 check run 才可能是被顶替的旧运行留下的：被顶替的
    那次会被取消，还在跑的不可能已经被顶替。全绿、还在跑的稳态因此不多花这一次
    调用 —— PR 每 60 秒轮询一次，那条路必须还是一问一答。
    """
    return any(
        isinstance(run, dict)
        and run.get("status") == "completed"
        and run.get("conclusion") not in _FINE_CONCLUSIONS
        for run in runs
    )


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def superseded_runs(workflow_runs: Iterable[dict], referenced: set[int]) -> set[int]:
    """`referenced` 里被同一个 workflow 的更新一次运行顶掉的那些运行 id。

    「更新」按 `(run_number, run_attempt, created_at)` 比：同一个 workflow 在同一
    个提交上的两次运行，run_number 一定不同。列表里认不出来的运行不动它 —— 宁可
    照旧报红，也不凭一次读不到就把红抹掉。
    """
    newest: dict[Any, tuple[tuple[int, int, str], int]] = {}
    workflow_of: dict[int, Any] = {}
    for row in workflow_runs:
        run_id, workflow = row.get("id"), row.get("workflow_id")
        if not isinstance(run_id, int) or workflow is None:
            continue
        workflow_of[run_id] = workflow
        rank = (
            _as_int(row.get("run_number")),
            _as_int(row.get("run_attempt")),
            str(row.get("created_at") or ""),
        )
        if workflow not in newest or rank > newest[workflow][0]:
            newest[workflow] = (rank, run_id)
    return {
        run_id
        for run_id in referenced
        if run_id in workflow_of and run_id != newest[workflow_of[run_id]][1]
    }


async def _workflow_runs_page(
    *,
    api_base: str,
    owner: str,
    repo: str,
    ref: str,
    headers: dict[str, str],
    transport: httpx.AsyncBaseTransport | None,
    page: int,
) -> list[dict] | None:
    """One page of this commit's Actions runs; None when GitHub won't say."""
    try:
        async with forge_client(transport=transport, timeout=20.0) as client:
            resp = await client.get(
                f"{api_base.rstrip('/')}/repos/{owner}/{repo}/actions/runs",
                headers=headers,
                params={"head_sha": ref, "per_page": _PAGE_SIZE, "page": page},
            )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    rows = resp.json().get("workflow_runs")
    if not isinstance(rows, list):
        return None
    return [row for row in rows if isinstance(row, dict)]


async def drop_superseded(
    runs: list[dict],
    api_base: str,
    owner: str,
    repo: str,
    ref: str,
    headers: dict[str, str],
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[dict]:
    """`runs` 去掉属于被顶替运行的那些（见模块 docstring）。

    `api_base`/`headers`/`transport` 就是读那份运行列表要用的东西 —— 调用方手里
    的客户端一个不少，参数照客户端自己的字段给。读不到就原样返回：多一条假红比
    少一条真红好收拾。
    """
    if not needs_resolution(runs):
        return runs
    referenced = {run_id for run in runs if (run_id := actions_run_id(run)) is not None}
    collected: list[dict] = []
    seen: set[int] = set()
    for page in range(1, _MAX_PAGES + 1):
        rows = await _workflow_runs_page(
            api_base=api_base,
            owner=owner,
            repo=repo,
            ref=ref,
            headers=headers,
            transport=transport,
            page=page,
        )
        if rows is None:
            return runs
        collected += rows
        seen |= {row["id"] for row in rows if isinstance(row.get("id"), int)}
        if referenced <= seen or len(rows) < _PAGE_SIZE:
            break
    dropped = superseded_runs(collected, referenced)
    if not dropped:
        return runs
    return [run for run in runs if actions_run_id(run) not in dropped]
