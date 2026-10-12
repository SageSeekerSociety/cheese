"""The `retro` subcommand of trace-sweep.py: a dispatch window's numbers as tables.

Run it through trace-sweep.py, which passes its credentials on:

    python3 .claude/scripts/trace-sweep.py retro --since 2026-10-12T11:00 --until 2026-10-12T14:05
    python3 .claude/scripts/trace-sweep.py retro --since ... --until ... --room <channel-id> [--json]
    python3 .claude/scripts/trace-sweep.py retro --self-test

It answers the questions of the orchestrator's 3-hour review (library doc
《总调度复盘 10-12 11:00–14:05（UTC）》, section 1 and the per-line table):

  * which PRs merged inside the window;
  * how many passed required CI on the first push, strictly and leniently
    (ignoring the round a newer push cancelled);
  * CI rounds per PR, by push and counting re-runs;
  * merge-queue drops on a failure, and the first failing check each time;
  * card filed to accepted, split into waiting for CI and waiting for review;
  * task start to first PR;
  * unfinished turns by seat and by reason (断流 and the rest).

Every count is defined in the 口径 section of the report. `gh` does the GitHub
reads; the platform half (--room) reads OKCHEESE_TOKEN, which trace-sweep.py
sets from its own login. Times without a zone are UTC.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

# --------------------------------------------------------------------------
# Fixed names this repo's CI defines. required-ci.yml names its own aggregating
# job `CI required` (full tier: a merge_group entry or a non-draft PR) and
# `CI required (draft quick)` (draft tier). Those two literals are the only
# check names this script trusts, and they are read from the PR head's
# check-runs — see .github/workflows/required-ci.yml.
# --------------------------------------------------------------------------
REQUIRED_FULL = "CI required"
REQUIRED_DRAFT = "CI required (draft quick)"
REQUIRED_NAMES = (REQUIRED_FULL, REQUIRED_DRAFT)

#: A check-run conclusion that means "the round ended and CI did not pass".
RED = ("failure", "timed_out", "startup_failure", "action_required")

# --------------------------------------------------------------------------
# Turn events, mirroring the backend's own constants (do not re-invent names).
#   * FAILED_TURN_EVENTS — backend/app/domain/block/indexed_rows.py: the 看板's
#     "this turn broke" pair.
#   * UNFINISHED_TURN_EVENTS — FAILED_TURN_EVENTS plus the two the platform
#     records as warnings (超时 / 部署打断): "the turn did not finish", which is
#     the retro's 「本轮未完成」. Kept separate so the 看板 number and this one
#     can be told apart instead of argued about.
# 「断流」 is not an event_type at all — it is `code == response_truncated` on a
# platform_error block (backend/app/domain/agent/platform_failures.py).
# --------------------------------------------------------------------------
FAILED_TURN_EVENTS = ("turn_failed", "platform_error")
UNFINISHED_TURN_EVENTS = FAILED_TURN_EVENTS + ("turn_timeout", "deploy_interrupted")
TRUNCATED_CODE = "response_truncated"
_STREAM_CUT = re.compile(r"Stream ended without receiving|Stream idle timeout", re.IGNORECASE)
# What an unclassified failure's service words say, first match wins. Read off
# the 10-12 window: these four covered every unfinished turn in it.
REASONS = (
    ("断流", _STREAM_CUT),
    ("出口离线", re.compile(r"egress is offline", re.IGNORECASE)),
    ("流中止", re.compile(r"aborted_streaming", re.IGNORECASE)),
    ("没开始处理", re.compile(r"未能开始处理")),
)


def failure_reason(code: str, text: str) -> str:
    if code == TRUNCATED_CODE:
        return "断流"
    for name, pattern in REASONS:
        if pattern.search(text):
            return name
    return code


#: Two rounds on one head are told apart by their check-run start second; jobs
#: of ONE workflow run start within a couple of seconds of each other. 60s is
#: wide enough to keep a run together and narrow enough to keep a draft-quick
#: run and the full run that follows `ready_for_review` apart (they are
#: minutes apart in practice).
ROUND_GAP_SECONDS = 60

API = os.environ.get("OKCHEESE_API", "https://okcheese.com/api")


# ==========================================================================
# GitHub half — everything goes through the `gh` CLI.
# ==========================================================================


class GhError(RuntimeError):
    pass


#: Where `--cache` keeps `gh` answers, so re-running a window against the slow
#: proxy does not re-fetch what it already has. None = no cache (the default:
#: this script, like trace-sweep.py, writes nothing unless told to).
CACHE_DIR: str | None = None


def _cache_path(key: str) -> str:
    import hashlib

    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode()).hexdigest() + ".json")


def _gh(args: list[str], attempts: int = 4) -> object:
    """Run `gh`, parse its stdout as JSON, retrying a transient failure.

    The platform proxies GitHub, so a call occasionally comes back `EOF` /
    `502` — real GitHub answers on the second or third try, and a whole retro
    should not die on one dropped read. `gh api` prints bare JSON; this
    environment's wrapper (and a few CI images) prepend a one-line banner, so
    the first `{`/`[` is found instead of assuming column 0. Pagination is left
    to the caller, and an answer is cached on disk only when `--cache` named a
    directory.

    The exit code decides whether there is an answer at all. `gh api` prints the
    API's error body — itself a JSON object — on stdout for a 403/404/422, so
    parsing without looking at the exit code would hand the caller an error
    object that every consumer here reads as "no data": a rate-limited call
    would silently drop that head's CI rounds from the counts, and under
    `--cache` the same wrong answer would be frozen and re-served on every later
    run. Only a zero exit is data; anything else is a failed attempt, retried
    like a dropped read and then raised.
    """
    key = " ".join(args)
    if CACHE_DIR:
        path = _cache_path(key)
        if os.path.exists(path):
            try:
                with open(path) as fh:
                    return json.load(fh)
            except (OSError, json.JSONDecodeError):
                pass
    last = ""
    for attempt in range(attempts):
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            last = f"exit {proc.returncode}: " + (proc.stderr or proc.stdout).strip()[:200]
            if attempt + 1 < attempts:
                time.sleep(2 * (attempt + 1))
            continue
        out = proc.stdout
        for ch in ("{", "["):
            i = out.find(ch)
            if i >= 0:
                try:
                    parsed = json.loads(out[i:])
                except json.JSONDecodeError:
                    continue
                if CACHE_DIR:
                    os.makedirs(CACHE_DIR, exist_ok=True)
                    with open(_cache_path(key), "w") as fh:
                        json.dump(parsed, fh)
                return parsed
        last = "no JSON: " + (proc.stderr or out).strip()[:200]
        if attempt + 1 < attempts:
            time.sleep(2 * (attempt + 1))
    raise GhError(f"gh {' '.join(args)}: {attempts} tries, last {last}")


def _iso(text: str) -> datetime:
    """GitHub writes a trailing Z; the platform writes no zone at all. Both are UTC."""
    when = datetime.fromisoformat(text)
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def merged_prs(repo: str, since: datetime, until: datetime) -> list[dict]:
    """PRs merged inside [since, until], newest first.

    `gh pr list --state merged --search merged:A..B` uses GitHub's search
    qualifier, then the ISO timestamps are re-checked locally: the search index
    is minute-granular and inclusive, so the window edge can carry one PR in or
    out that the second-precise check settles.
    """
    span = f"{since.strftime('%Y-%m-%dT%H:%M:%S')}Z..{until.strftime('%Y-%m-%dT%H:%M:%S')}Z"
    raw = _gh(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "merged",
            "--limit",
            "200",
            "--search",
            f"merged:{span}",
            "--json",
            "number,title,mergedAt,headRefOid,mergeCommit",
        ]
    )
    out = []
    for pr in raw or []:
        at = pr.get("mergedAt")
        if not at:
            continue
        when = _iso(at)
        if since <= when <= until:
            out.append(pr)
    return sorted(out, key=lambda p: p["mergedAt"])


def pr_timeline(repo: str, number: int) -> list[dict]:
    """A PR's issue timeline, every page. The events this script reads:
    `committed` (a push's tip and any non-tip commit), `head_ref_force_pushed`
    (the new commit id), `added_to_merge_queue` / `removed_from_merge_queue`,
    `ready_for_review`, `merged`."""
    got = _gh(["api", f"repos/{repo}/issues/{number}/timeline?per_page=100", "--paginate"])
    if isinstance(got, dict):  # a single page came back as a bare object
        got = got.get("data", [])
    return [e for e in (got or []) if isinstance(e, dict)]


def heads_of(timeline: list[dict]) -> list[str]:
    """Every commit id that appears as this PR's head, in timeline order.

    `committed` fires for every commit in a push, not only the tip, so a push of
    three commits shows three ids; `head_ref_force_pushed` carries the id a
    force-push moved the branch to. Which ids were really pushed-to heads is
    settled by CI in `pr_report` (only the tip of a push gets check-runs), not
    here — this function only collects the candidates.
    """
    heads: list[str] = []
    seen: set[str] = set()
    for e in timeline:
        sha = None
        if e.get("event") == "committed":
            sha = e.get("sha")
        elif e.get("event") == "head_ref_force_pushed":
            sha = e.get("commit_id")
        if sha and sha not in seen:
            seen.add(sha)
            heads.append(sha)
    return heads


def required_checks(repo: str, sha: str) -> list[dict]:
    """This commit's required-ci check-runs: `{name, conclusion, started_at}`.

    One per workflow run that touched the commit (a head can carry two — a
    draft-quick run, then a full run after `ready_for_review`). Anything not
    named `CI required` / `CI required (draft quick)` is dropped here and never
    influences a count.
    """
    data = _gh(["api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100"])
    runs = (data or {}).get("check_runs", []) if isinstance(data, dict) else []
    return [
        {
            "name": c["name"],
            "conclusion": c.get("conclusion"),
            "started_at": c.get("started_at") or "",
            "completed_at": c.get("completed_at") or "",
        }
        for c in runs
        if c.get("name") in REQUIRED_NAMES
    ]


def ci_rounds(checks: list[dict]) -> list[dict]:
    """Fold one head's required-ci check-runs into rounds, oldest first.

    A "round" is one required-ci workflow run. Its two jobs (`required` and
    `required-draft`) start within a second of each other, so they are grouped
    by start time; the round's outcome is the strongest verdict among them:
    green if any job succeeded, red if any failed, else cancelled, else skipped
    (a round whose only jobs were skipped — the tier the event did not select —
    is not a round and is dropped).
    """
    buckets: list[list[dict]] = []
    for c in sorted(checks, key=lambda x: x["started_at"]):
        if buckets:
            last = buckets[-1][-1]
            gap = _seconds(last["started_at"], c["started_at"])
            if gap is not None and gap <= ROUND_GAP_SECONDS:
                buckets[-1].append(c)
                continue
        buckets.append([c])
    out = []
    for b in buckets:
        concl = {c["conclusion"] for c in b}
        if "success" in concl:
            outcome = "green"
        elif concl & set(RED):
            outcome = "red"
        elif "cancelled" in concl:
            outcome = "cancelled"
        else:
            outcome = "skipped"
        if outcome == "skipped":
            continue
        # Which tier this round judged: the job that actually ran (not the
        # sibling the event skipped). A draft head runs `required-draft` and
        # leaves `required` skipped, and vice versa.
        deciding = [c for c in b if c["conclusion"] not in (None, "skipped")]
        tier = "draft" if deciding and all(c["name"] == REQUIRED_DRAFT for c in deciding) else "full"
        out.append(
            {
                "outcome": outcome,
                "tier": tier,
                "started_at": b[0]["started_at"],
                "completed_at": max((c.get("completed_at") or "" for c in b), default=""),
                "jobs": [{"name": c["name"], "conclusion": c["conclusion"]} for c in b],
            }
        )
    return out


def _seconds(a: str, b: str) -> float | None:
    try:
        return abs((_iso(a) - _iso(b)).total_seconds())
    except ValueError:
        return None


def merge_group_runs(repo: str, since: datetime, until: datetime) -> dict[int, list[dict]]:
    """Merge-queue workflow runs created in the window, keyed by PR number.

    A merge_queue run's branch is `gh-readonly-queue/main/pr-<N>-<base>`, so the
    PR number is read off the branch. `created` bounds the query to the window;
    the queue never carries a run across the window edge that matters here.
    """
    spread = (
        f"{(since - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        f"..{(until + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    # Paged by hand, not with `--paginate`: a JSON-OBJECT endpoint makes
    # `--paginate` concatenate objects (not an array) and output stops being
    # parseable. A 3h window holds tens of runs, so a handful of pages is all.
    runs = []
    for page in range(1, 11):
        data = _gh(
            [
                "api",
                f"repos/{repo}/actions/runs?event=merge_group&created={spread}&per_page=100&page={page}",
            ]
        )
        batch = (data or {}).get("workflow_runs", []) if isinstance(data, dict) else []
        runs.extend(batch)
        if len(batch) < 100:
            break
    by_pr: dict[int, list[dict]] = {}
    for r in runs:
        branch = r.get("head_branch") or ""
        n = None
        if "/pr-" in branch:
            tail = branch.split("/pr-", 1)[1]
            n = int(tail.split("-", 1)[0]) if tail[:1].isdigit() else None
        if n is None:
            continue
        by_pr.setdefault(n, []).append(
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "head_sha": r.get("head_sha"),
                "conclusion": r.get("conclusion"),
            }
        )
    for runs in by_pr.values():
        runs.sort(key=lambda x: x["created_at"])
    return by_pr


def run_jobs(repo: str, run_id: int) -> list[dict]:
    """A workflow run's jobs: `{name, conclusion, started_at, completed_at}`.

    `check-runs` on the merge-group commit gives the same names but only for
    checks that reported; the jobs endpoint also lists ones that were skipped or
    never started, which is what makes "which check failed first" answerable.
    """
    data = _gh(["api", f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"])
    jobs = (data or {}).get("jobs", []) if isinstance(data, dict) else []
    return [
        {
            "name": j.get("name"),
            "conclusion": j.get("conclusion"),
            "started_at": j.get("started_at") or "",
            "completed_at": j.get("completed_at") or "",
        }
        for j in jobs
    ]


def first_error(jobs: list[dict]) -> str | None:
    """The failing job that finished first, by completion time.

    Not the most important one — the order the forge reports failures is not
    their order of importance, and the aggregate `CI required` always finishes
    last. It is the one the retro sheet wants: the earliest thing that went red
    in that entry, so a reader can see WHERE it broke. A run whose only failure
    is the aggregate `CI required` (every suite green) points at the job's own
    steps — the commit-title check lives there.
    """
    failed = [j for j in jobs if j["conclusion"] in RED]
    if not failed:
        return None
    failed.sort(key=lambda j: j["completed_at"] or j["started_at"] or "")
    return failed[0]["name"]


def merge_queue_exits(
    timeline: list[dict], runs: list[dict], repo: str, since: datetime, until: datetime
) -> list[dict]:
    """Every time the PR left the merge queue inside the window, and whether that
    was a failure.

    The timeline comes back whole for a PR that only merged inside the window,
    so the window is applied here: an exit the PR took before it is not this
    window's.

    A `removed_from_merge_queue` event is the queue letting go of the PR. It is
    NOT a failure by itself: the queue also removes an entry at the instant it
    merges it (`merged` fires in the same second), and that is the happy path.
    The event the forge writes carries no `reason` field (verified against this
    repo's API: the REST timeline event has only actor/created_at), so the
    reason is read off the merge_group run this entry created: the latest run
    for the PR started at or before the removal. Its conclusion decides, and its
    failing jobs are the "which check" the sheet prints.

    `failed` = that run concluded failure. This is the reading that reproduces
    the retro's 「掉出合并队列（失败）3 次，全是 #3496」.
    """
    out = []
    for e in timeline:
        if e.get("event") != "removed_from_merge_queue":
            continue
        at = e.get("created_at")
        if not at or not (since <= _iso(at) <= until):
            continue
        entry = None
        for r in runs:
            if r["created_at"] <= at:
                entry = r
            else:
                break
        jobs = run_jobs(repo, entry["id"]) if entry else []
        failed_jobs = [j["name"] for j in jobs if j["conclusion"] in RED]
        out.append(
            {
                "at": at,
                "run_id": entry["id"] if entry else None,
                "run_conclusion": entry["conclusion"] if entry else None,
                "failed_checks": failed_jobs,
                "first_error": first_error(jobs),
                "failed": bool(entry and entry["conclusion"] == "failure"),
            }
        )
    return out


def pr_report(pr: dict, heads: list[dict], exits: list[dict]) -> dict:
    """One merged PR's row, assembled from what `pr_reports` fetched.

    Kept apart from the fetching so the 首推 wiring can be asserted without a
    network: `heads` is one entry per id `heads_of` found, in timeline order,
    each carrying its `rounds`.

    Only the heads CI ran on are pushes, one per push: `required-ci.yml`
    triggers on `pull_request: synchronize` with no path filter, so every push
    to the PR branch ran it once, on the pushed-to commit. A multi-commit push
    is therefore one round, and its non-tip commits (which ran no CI at all)
    are dropped here rather than counted — which is also why 首推 judges
    `pushes[0]`, not `heads[0]`.
    """
    pushes = [h for h in heads if h["rounds"]]
    first = pushes[0] if pushes else None
    return {
        "number": pr["number"],
        "title": pr.get("title"),
        "merged_at": pr.get("mergedAt"),
        "merge_commit": (pr.get("mergeCommit") or {}).get("oid"),
        "pushes": len(pushes),
        "ci_runs": sum(len(p["rounds"]) for p in pushes),
        "push_detail": pushes,
        "first_head": first["sha"] if first else None,
        "strict_first_pass": strict_pass(first) if first else False,
        "lenient_first_pass": _lenient(first["rounds"] if first else []),
        "exits": exits,
    }


def pr_reports(repo: str, since: datetime, until: datetime) -> list[dict]:
    """One record per merged PR, every GitHub-side fact the retro needs."""
    prs = merged_prs(repo, since, until)
    mg = merge_group_runs(repo, since, until)
    reports = []
    for pr in prs:
        number = pr["number"]
        timeline = pr_timeline(repo, number)
        heads = [
            {"sha": sha, "rounds": ci_rounds(required_checks(repo, sha))} for sha in heads_of(timeline)
        ]
        reports.append(
            pr_report(pr, heads, merge_queue_exits(timeline, mg.get(number, []), repo, since, until))
        )
    return reports


def _lenient(first_push: list[dict]) -> bool:
    """The 宽松 reading of 首推一次过 CI, and why it is these two exclusions.

    「严格」= the first head's FIRST round is green. That counts two things
    against the push which are not quite "the code failed":

      * a round `cancelled` because a newer push superseded it — the check never
        reached a verdict, and
      * a draft-tier round (`CI required (draft quick)`), which is the draft's
        own bounded check, not the `CI required` branch protection reads.

    So 宽松 drops both and asks a narrower question: did a GREEN full-tier run
    ever exist on the FIRST head? If the first push's full check was skipped
    (still a draft) or only ever cancelled, the push did not pass — a re-push
    that went green does not count here, which is what keeps this from becoming
    "did it eventually pass". #3497 / #3492 (same head, cancelled then green)
    and #3496 (same head, draft red then full green) pass; #3498 / #3495 (first
    head only ever cancelled) do not.

    On the 10-12 window this reads 8/12 = 67%, one more than the orchestration
    review's 7/12: the review took the first commit of each PR's push list as the
    first head, and for #3493 that commit is one CI never ran, so the head the
    required check actually judged was never read. The first head CI ran on is
    the one that counts.
    """
    if not first_push:
        return False
    remaining = [r for r in first_push if r["outcome"] != "cancelled" and r.get("tier") == "full"]
    return bool(remaining) and remaining[0]["outcome"] == "green"


# ==========================================================================
# Platform half — the accept cards, the task clock, the turns that failed.
# `_request` and `token_from_env` below serve a standalone run of this file. A
# run driven by trace-sweep.py gets that module's copies instead (its `retro()`
# writes them into this module), so the sweep's password login and its 401/403
# message are what such a run uses.
# ==========================================================================


def _request(method: str, path: str, token: str | None, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)["data"]


def token_from_env() -> str | None:
    """OKCHEESE_TOKEN only. No password path: the retro is a read, and a read
    that needs a second factor is a read that should be handed a token."""
    return os.environ.get("OKCHEESE_TOKEN") or None


def _data(payload):
    """Unwrap the platform's `{data: ...}` envelope, paged or not."""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def room_tasks(room: str, token: str) -> list[dict]:
    """Every task in a channel: `GET /topics/{room}/tasks?limit=0` (roster shape,
    no conversation). The fields the retro reads are `number`, `title`,
    `agent_handle`, `started_at`, `pr_number` — the TaskOut contract
    (backend/app/domain/room_task/schemas.py)."""
    rows = _data(_request("GET", f"/topics/{room}/tasks?limit=0", token))
    return rows if isinstance(rows, list) else []


def task_cards(task_id: str, token: str) -> list[dict]:
    """One task's accept cards: `GET /topics/{task}/accept-card`.

    A task IS a conversation, so its own id is the topic the card read takes.
    Each card carries `created_at` (递卡), `decided_at` (采纳, overwritten by a
    later revoke — see platform_stats/pipeline.py's caveat), `status`,
    `pr_number`, `pr_head_sha`. `status == accepted` is what counts as 采纳."""
    rows = _data(_request("GET", f"/topics/{task_id}/accept-card", token))
    if isinstance(rows, dict):
        rows = rows.get("items", [])
    return rows if isinstance(rows, list) else []


def turn_failures(task_id: str, token: str, since: str, until: str) -> list[dict]:
    """Turns that did not finish, inside the window, for one task.

    The structured source is `blocks.meta`: a broken turn's block carries
    `event_type` in (`turn_failed` = 没归类的失败, `platform_error` = 归了类的平台故障
    — backend/app/domain/block/indexed_rows.py FAILED_TURN_EVENTS) plus `platform_error`
    上的 `code`（backend/app/domain/agent/platform_failures.py）. `turn_timeout`
    （一个字都没输出 / 超时）和 `deploy_interrupted`（部署打断）也是「这轮没完成」，
    但后端把它们记成 warning（平台自己接着走），所以这里**分开点名**，不和失败混成一个数。
    这些块在任务的 transcript 里，就是 `GET /topics/{task}/transcript` 翻的那些。
    「断流」（模型流被打断）归的码是 `response_truncated`。

    This is NOT the same scan trace-sweep.py does: that one reads `meta.error` off
    tool calls and classifies the message text; this one reads the turn's own
    `event_type`/`code`, which is the structured source. Same limit either way,
    copied from platform_stats/pipeline.py: a client-side filter over event
    blocks, not an indexed read.
    """
    found = []
    before = None
    for _ in range(100):
        query = f"/topics/{task_id}/transcript?limit=200" + (f"&before={before}" if before else "")
        page = _request("GET", query, token)
        items = (page or {}).get("data", [])
        oldest = None
        for block in items:
            meta = block.get("meta") or {}
            at = (meta.get("at") or block.get("created_at") or "")[:19]
            oldest = at if oldest is None or at < oldest else oldest
            event = meta.get("event_type")
            if event in UNFINISHED_TURN_EVENTS and since <= at <= until:
                found.append(
                    {
                        "at": at,
                        "code": meta.get("code") or "unclassified",
                        # An unclassified failure keeps the service's words in its
                        # detail; a model stream cut off says so there.
                        "reason": failure_reason(
                            meta.get("code") or "unclassified",
                            str(block.get("content") or "") + json.dumps(meta, ensure_ascii=False),
                        ),
                        "event_type": event,
                        "severity": meta.get("severity"),
                    }
                )
        before = (page or {}).get("oldest_id")
        if not (page or {}).get("has_more") or not before or (oldest and oldest < since):
            return found
    return found


def collect_platform(room: str, token: str, since: str, until: str) -> dict:
    """The platform half: cards (递卡/采纳 by PR), the task clock, and the turns
    that failed by seat. All times are ISO strings sliced to the minute; the
    GitHub half is joined on `pr_number`."""
    tasks = room_tasks(room, token)
    cards = []
    failed_turns = []
    for task in tasks:
        tid = task.get("id")
        if not tid:
            continue
        seat = task.get("agent_handle") or task.get("owner_handle") or "?"
        for card in task_cards(tid, token):
            created = card.get("created_at") or ""
            decided = card.get("decided_at") or ""
            if not ((since <= created[:19] <= until) or (since <= decided[:19] <= until)):
                continue
            cards.append(
                {
                    "task": task.get("number"),
                    "seat": seat,
                    "pr_number": card.get("pr_number"),
                    "status": str(card.get("status")),
                    "filed_at": created[:19] or None,
                    "decided_at": decided[:19] or None,
                    "head_sha": card.get("pr_head_sha"),
                }
            )
        for f in turn_failures(tid, token, since, until):
            failed_turns.append({**f, "task": task.get("number"), "seat": seat})
    return {
        "tasks": [
            {
                "number": t.get("number"),
                "seat": t.get("agent_handle") or t.get("owner_handle") or "?",
                "started_at": (t.get("started_at") or "")[:19] or None,
                "pr_number": t.get("pr_number"),
                "status": str(t.get("status")),
            }
            for t in tasks
        ],
        "cards": cards,
        "failed_turns": failed_turns,
    }


# ==========================================================================
# Joining the two halves.
# ==========================================================================


def green_of(report: dict) -> str | None:
    """When this PR's head first reached green required CI — the boundary the
    递卡→采纳 split is measured at.

    "First green" = the earliest FULL-tier round on the FINAL head whose outcome
    is green. The final head is the one that merged, so its green is the one the
    accept click waited for; an earlier head that went green and was then pushed
    over is not what CI was pending on. The tier matters for the same reason it
    does in `_lenient`: a draft head runs `CI required (draft quick)` on the very
    commit that later runs full, and the accept was blocked until the full one —
    a green draft round is not the boundary."""
    pushes = report.get("push_detail") or []
    if not pushes:
        return None
    last = pushes[-1]  # push_detail holds heads CI ran on, so the last is the merged head
    # When the required check reported green, not when its round started.
    greens = [
        r.get("completed_at") or r["started_at"]
        for r in last["rounds"]
        if r["outcome"] == "green" and r.get("tier") == "full"
    ]
    return min(greens) if greens else None


def join_cards(cards: list[dict], reports: list[dict]) -> list[dict]:
    """Attach each accepted card's two waits.

    「等 CI 绿」 = green − filed, or 0 when the head was already green at filing;
    「CI 绿后等总调度」 = decided − the later of filed and green. The two add up
    to filed → decided. Either is None when an end is unknown (no head, no
    green, still pending): None, never 0, so an average never swallows a hole."""
    by_pr = {r["number"]: r for r in reports}
    out = []
    for card in cards:
        row = dict(card)
        green = green_of(by_pr.get(card.get("pr_number"), {}))
        row["green_at"] = green
        filed = card.get("filed_at")
        to_green = _minutes(filed, green)
        row["wait_ci_minutes"] = None if to_green is None else max(0.0, to_green)
        ready = green if to_green is not None and to_green > 0 else (filed if green else None)
        row["wait_scheduler_minutes"] = _minutes(ready, card.get("decided_at"))
        out.append(row)
    return out


def _minutes(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    try:
        return round((_iso(b) - _iso(a)).total_seconds() / 60.0, 1)
    except ValueError:
        return None


def first_pr_lag(tasks: list[dict], cards: list[dict], since: str = "", until: str = "~") -> list[dict]:
    """开工→首个 PR per task: `started_at` of the task to the earliest card it
    filed. Tasks with no start or no card are listed with None rather than
    dropped, because "there is no PR" is itself the finding."""
    first_card: dict = {}
    for c in cards:
        if c.get("filed_at") and (c["task"] not in first_card or c["filed_at"] < first_card[c["task"]]):
            first_card[c["task"]] = c["filed_at"]
    out = []
    for t in tasks:
        started, first = (t.get("started_at") or "")[:19], (first_card.get(t.get("number")) or "")[:19]
        # Only tasks that started, or filed their first card, inside the window.
        if not (since <= started <= until or (first and since <= first <= until)):
            continue
        out.append(
            {
                "task": t.get("number"),
                "seat": t.get("seat"),
                "started_at": t.get("started_at"),
                "first_pr_at": first_card.get(t.get("number")),
                "lag_minutes": _minutes(t.get("started_at"), first_card.get(t.get("number"))),
            }
        )
    return out


def lines_table(tasks: list[dict], cards: list[dict], reports: list[dict]) -> list[dict]:
    """各线表: one row per task (线) that moved in the window.

    「本窗口结果」 is composed from facts, not prose: which of this task's cards
    were accepted in the window, which of their PRs merged (joined to the GitHub
    half by pr_number), and the task's status right now. A task nobody touched
    is not a 线 that moved and is left out."""
    merged = {r["number"] for r in reports}
    by_task: dict = {}
    for c in cards:
        by_task.setdefault(c["task"], []).append(c)
    out = []
    for t in tasks:
        mine = by_task.get(t["number"])
        if not mine:
            continue
        accepted = [c for c in mine if c.get("status") == "accepted"]
        merged_prs = sorted({c["pr_number"] for c in mine if c.get("pr_number") in merged})
        out.append(
            {
                "task": t["number"],
                "seat": t["seat"],
                "status": t["status"],
                "cards": len(mine),
                "accepted": len(accepted),
                "merged_prs": merged_prs,
                "result": (
                    (f"合入 {', '.join('#' + str(p) for p in merged_prs)}；" if merged_prs else "")
                    + f"本窗口 {len(mine)} 张卡、采纳 {len(accepted)} 张，任务现在 {t['status']}"
                ),
            }
        )
    return out


def turns_by_seat(failed_turns: list[dict]) -> dict[str, dict]:
    """Unfinished turns grouped by seat, then by code.

    `broken` counts only the 看板's pair (turn_failed / platform_error — a real
    break); `total` is every event in UNFINISHED_TURN_EVENTS (超时 / 部署打断 ride
    along). `truncated` pulls out `response_truncated`, the 「断流」 the retro names,
    so the three numbers the retro quotes — 未完成, 断流, and the 看板's own count —
    can each be read off directly instead of recomputed by hand."""
    out: dict[str, dict] = {}
    for f in failed_turns:
        row = out.setdefault(
            f["seat"], {"total": 0, "broken": 0, "truncated": 0, "by_code": {}, "tasks": set()}
        )
        row["total"] += 1
        if f.get("event_type") in FAILED_TURN_EVENTS:
            row["broken"] += 1
        reason = f.get("reason") or failure_reason(f["code"], "")
        if reason == "断流":
            row["truncated"] += 1
        row["by_code"][reason] = row["by_code"].get(reason, 0) + 1
        if f.get("task") is not None:
            row["tasks"].add(f["task"])
    for row in out.values():
        row["tasks"] = sorted(row["tasks"])
    return out


# ==========================================================================
# Rendering.
# ==========================================================================


def _pct(n: int, d: int) -> str:
    return "—" if not d else f"{n}/{d} = {round(100 * n / d)}%"


def _median(values: list[float]) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else round((vals[mid - 1] + vals[mid]) / 2, 1)


def _stats(values: list[float | None]) -> dict:
    """Median, mean and max of the values that are known, in minutes."""
    known = [v for v in values if v is not None]
    return {
        "n": len(known),
        "median": _median(known),
        "mean": round(sum(known) / len(known), 1) if known else None,
        "max": max(known, default=None),
    }


def summarize(
    reports: list[dict], window_cards: list[dict] | None = None, since: str = "", until: str = "~"
) -> dict:
    """The numbers on the headline table. `window_cards` is the platform half's
    accepted cards, or None when that half did not run."""
    n = len(reports)
    strict = sum(1 for r in reports if r["strict_first_pass"])
    lenient = sum(1 for r in reports if r["lenient_first_pass"])
    failed_exits = [e for r in reports for e in r["exits"] if e["failed"]]
    out = {
        "merged_prs": n,
        "strict_first_pass": {"n": strict, "pct": _pct(strict, n)},
        "lenient_first_pass": {"n": lenient, "pct": _pct(lenient, n)},
        "pushes_per_pr": round(sum(r["pushes"] for r in reports) / n, 2) if n else None,
        "ci_runs_per_pr": round(sum(r["ci_runs"] for r in reports) / n, 2) if n else None,
        "merge_queue_failed_exits": len(failed_exits),
        "merge_queue_failed_prs": sorted(
            {r["number"] for r in reports if any(e["failed"] for e in r["exits"])}
        ),
    }
    if window_cards is not None:
        # Cards accepted inside the window, like the merged PRs.
        acc = [
            c
            for c in window_cards
            if c.get("status") == "accepted" and since <= (c.get("decided_at") or "") <= until
        ]
        out["accept_dwell"] = {
            "n": len(acc),
            "total": _stats([_minutes(c.get("filed_at"), c.get("decided_at")) for c in acc]),
            "wait_ci": _stats([c["wait_ci_minutes"] for c in acc]),
            "wait_review": _stats([c["wait_scheduler_minutes"] for c in acc]),
        }
    return out


def render_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        f"# 复盘窗口 {report['since']} ~ {report['until']} UTC",
        "",
        "## 口径",
        "",
        (
            "- **合并 PR**：`gh pr list --state merged --search merged:<窗口>`，再按 `mergedAt` 逐条复核"
            "（搜索索引是分钟粒度、两端都含，边界靠秒级判定收口）。"
        ),
        (
            "- **首推（严格）**：该 PR 第一个 head 的第一轮必需 CI 就 `success`。被新推顶掉的 "
            "`cancelled` 轮算失败——那次推送没过，是被换掉了。「第一个 head」指第一个跑过必需 CI 的 "
            "head：一次推多个提交时，非尖端提交不会跑 CI，也就不是被检查判过的那个 head。"
        ),
        (
            "- **首推（宽松）**：只看第一个 head，去掉被新推顶掉的 `cancelled` 轮和 draft 层"
            "（`CI required (draft quick)`——那是草稿自己的检查，不是分支保护读的那个），第一个剩下的 full 轮"
            "是 `success` 才算过。第一个 head 的 full 检查被跳过（还在草稿）或只被 cancel 掉，就不算过——"
            "换个 head 才绿不计入，否则它就变成「最后过没过」了。"
        ),
        (
            "- **轮数（按推送）**：跑过必需 CI 的不同 head 数。`required-ci.yml` 在 `pull_request: synchronize` "
            "上跑、没有路径过滤，所以每次推送到 PR 分支都恰好跑一次、跑在被推上去的那个提交上；一次推多个提交时"
            "只有尖端提交拿到检查（`committed` 也给非尖端提交各发一条，那些不算），一次推送记一轮。"
        ),
        (
            "- **轮数（含重跑）**：所有 head 上跑过的必需 CI 工作流次数，同一 head 上的重跑"
            "（draft 轮 / `ready_for_review` 后的 full 轮）各算一次。"
        ),
        (
            "- **掉出合并队列（失败）**：窗口内的 `removed_from_merge_queue` 事件看它那次 entry 的 "
            "`merge_group` 运行——运行 `failure` 才算失败（正常合并时事件也在，运行是 `success`）。"
        ),
        "  事件本身 REST 里**没有** `reason` 字段（已核实），原因从运行的失败 check 读。",
        (
            "- **递卡→采纳**：窗口内采纳的卡，卡 `created_at`（第一次递）→ `decided_at`。拆成「等 CI 绿」"
            "（递卡到该 PR 最终 head 的 **full 层**必需检查报绿，递时已绿记 0；draft 层那次不算，"
            "采纳在草稿期是拦着的）和「CI 绿后等总调度」，两段相加等于全程。"
            "退回后再递的卡仍从第一次递算起，所以比按最后一次递卡时间算的长。"
        ),
        (
            "- **本轮未完成**：任务会话里 `meta.event_type ∈ {turn_failed, platform_error, turn_timeout, "
            "deploy_interrupted}` 的块，按座位归组。`turn_timeout`（超时/一个字没输出）和 `deploy_interrupted`"
            "（部署打断）平台记成 warning、自己接着走，所以单列「真失败」只数看板那两个（`turn_failed` / "
            "`platform_error`）。「断流」是模型流中断：`code = response_truncated`，或服务原话是 Stream ended / Stream idle timeout。"
        ),
        "",
        "## 总表",
        "",
        "| 指标 | 本窗口 |",
        "|---|---|",
        f"| 合并 PR | {s['merged_prs']} |",
        f"| 首推一次过 CI（严格） | {s['strict_first_pass']['pct']} |",
        f"| 首推一次过 CI（宽松） | {s['lenient_first_pass']['pct']} |",
        f"| 每 PR 轮数（按推送） | {s['pushes_per_pr']} |",
        f"| 每 PR 轮数（含重跑） | {s['ci_runs_per_pr']} |",
        f"| 掉出合并队列（失败） | {s['merge_queue_failed_exits']} 次"
        + (f"，全在 PR {s['merge_queue_failed_prs']}" if s["merge_queue_failed_prs"] else "")
        + " |",
    ]
    if "accept_dwell" in s:
        for label, key in (
            ("递卡→采纳", "total"),
            ("　其中等 CI 绿", "wait_ci"),
            ("　其中 CI 绿后等总调度", "wait_review"),
        ):
            d = s["accept_dwell"][key]
            lines.append(
                f"| {label} | 中位 {d['median']} 分、均值 {d['mean']}、最慢 {d['max']}（n={d['n']}） |"
            )
    else:
        lines.append("| 递卡→采纳 | 未测（没有 OKCHEESE_TOKEN，平台那一半没跑） |")
    lines += ["", "## 明细（每 PR 一行）", ""]
    lines += [
        "| PR | 合并时刻 | 推送 | 轮数 | 首推严格 | 首推宽松 | 出队失败 | 首个错误 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in report["prs"]:
        failed = [e for e in r["exits"] if e["failed"]]
        err = next((e["first_error"] for e in failed if e["first_error"]), "—")
        lines.append(
            f"| #{r['number']} | {r['merged_at']} | {r['pushes']} | {r['ci_runs']} | "
            f"{'✓' if r['strict_first_pass'] else '✗'} | "
            f"{'✓' if r['lenient_first_pass'] else '✗'} | {len(failed)} | {err} |"
        )
    lines += ["", "## 合并队列出队明细", ""]
    lines += ["| PR | 出队时刻 | 运行 | 结论 | 失败 check | 首个错误 |", "|---|---|---|---|---|---|"]
    for r in report["prs"]:
        for e in r["exits"]:
            if not e["failed"]:
                continue
            lines.append(
                f"| #{r['number']} | {e['at']} | {e['run_id']} | {e['run_conclusion']} | "
                f"{', '.join(e['failed_checks']) or '—'} | {e['first_error'] or '—'} |"
            )
    if report.get("platform"):
        p = report["platform"]
        lines += ["", "## 各线表（线=任务）", "", "| 线 | 座位 | 状态 | 本窗口结果 |", "|---|---|---|---|"]
        for row in p["lines"]:
            lines.append(f"| #{row['task']} | {row['seat']} | {row['status']} | {row['result']} |")
        lines += [
            "",
            "## 开工→首个 PR",
            "",
            "| 任务 | 座位 | 开工 | 首个 PR | 间隔(分) |",
            "|---|---|---|---|---|",
        ]
        for row in p["first_pr_lag"]:
            lines.append(
                f"| #{row['task']} | {row['seat']} | {row['started_at'] or '—'} | "
                f"{row['first_pr_at'] or '—'} | {row['lag_minutes'] if row['lag_minutes'] is not None else '—'} |"
            )
        lines += [
            "",
            "## 本轮未完成（按座位）",
            "",
            (
                "「未完成」= UNFINISHED_TURN_EVENTS（turn_failed / platform_error / turn_timeout / "
                "deploy_interrupted）；「真失败」= 看板那两个（turn_failed / platform_error），超时和部署打断 "
                "平台自己接着走，不在这列；「断流」= 模型流中断（code 为 response_truncated，或服务原话是 Stream ended / Stream idle timeout）。"
            ),
            "",
            "| 座位 | 未完成 | 真失败 | 断流 | 按原因 | 涉及任务 |",
            "|---|---|---|---|---|---|",
        ]
        for seat, row in sorted(p["turns_by_seat"].items(), key=lambda kv: -kv[1]["total"]):
            codes = ", ".join(f"{k}×{v}" for k, v in sorted(row["by_code"].items(), key=lambda kv: -kv[1]))
            tasks = ", ".join(f"#{t}" for t in row["tasks"])
            lines.append(
                f"| {seat} | {row['total']} | {row['broken']} | {row['truncated']} | {codes} | {tasks} |"
            )
    else:
        lines += [
            "",
            "## 各线表 / 开工→首个 PR / 本轮未完成",
            "",
            "未测：没有 OKCHEESE_TOKEN（或没给 --room），平台那一半没跑。",
        ]

    lines += [
        "",
        "## 对照（与《总调度复盘 10-12》§1）",
        "",
        f"- 合并 PR：{s['merged_prs']}（复盘 12）",
        (
            f"- 每 PR 轮数：按推送 {s['pushes_per_pr']}、含重跑 {s['ci_runs_per_pr']}（复盘报 2.33 / 2.50）"
            "——差在「按推送」这一栏：把多提交推送按提交数算就是 28/12 = 2.33，按推送算（本窗口 #3487、"
            "#3493、#3498 各有一处多提交推送）是 25/12 = 2.08，以按推送为准。"
        ),
        f"- 掉出合并队列（失败）：{s['merge_queue_failed_exits']}（复盘 3，全是 #3496）",
        f"- 首推严格：{s['strict_first_pass']['pct']}（复盘 4/12 = 33%）",
        (
            f"- 首推宽松：{s['lenient_first_pass']['pct']}（复盘 7/12 = 58%）"
            "——把被顶掉的 cancelled 轮和 draft 层去掉后，第一个跑过必需 CI 的 head 上有没有一次绿的 "
            "full 必需检查。"
        ),
        "  这一栏的读法会差数：把提交列表的第一项当首个 head，和按「第一个跑过必需 CI 的 head」"
        "判，只有当第一项一次 CI 都没跑过、而那个跑过 CI 的 head 宽松算过时才不同——10-12 窗口的 "
        "#3493 就是这种，按 head 判它算过，复盘那 7 条里不算它。",
    ]
    return "\n".join(lines)


# ==========================================================================
# Entry points.
# ==========================================================================


def build(repo: str, since: datetime, until: datetime, room: str | None) -> dict:
    """Run both halves and join them. The GitHub half is always attempted; the
    platform half only when a room id and an OKCHEESE_TOKEN are both present."""
    reports = pr_reports(repo, since, until)
    platform = None
    if room:
        token = token_from_env()
        if token is None:
            sys.stderr.write(
                "no OKCHEESE_TOKEN: skipping the platform half (递卡→采纳 / 开工→首个 PR / 本轮未完成)\n"
            )
        else:
            since_s, until_s = since.strftime("%Y-%m-%dT%H:%M:%S"), until.strftime("%Y-%m-%dT%H:%M:%S")
            raw = collect_platform(room, token, since_s, until_s)
            joined = join_cards(raw["cards"], reports)
            platform = {
                "cards": joined,
                "first_pr_lag": first_pr_lag(raw["tasks"], raw["cards"], since_s, until_s),
                "lines": lines_table(raw["tasks"], raw["cards"], reports),
                "turns_by_seat": turns_by_seat(raw["failed_turns"]),
            }
    cards = platform["cards"] if platform else None
    return {
        "since": since.strftime("%Y-%m-%dT%H:%M:%S"),
        "until": until.strftime("%Y-%m-%dT%H:%M:%S"),
        "prs": reports,
        "platform": platform,
        "summary": summarize(
            reports, cards, since.strftime("%Y-%m-%dT%H:%M:%S"), until.strftime("%Y-%m-%dT%H:%M:%S")
        ),
    }


def retro_main(argv: list[str]) -> int:
    """The subcommand body. `trace-sweep.py` will call this with the args after
    its `retro` verb; standalone, `main()` below passes sys.argv[1:]."""
    if "--self-test" in argv:
        return self_test()
    parser = argparse.ArgumentParser(prog="retro", description="dispatch-window retro sheet")
    parser.add_argument("--repo", default="SageSeekerSociety/cheese")
    parser.add_argument("--since", required=True, help="window start, ISO UTC")
    parser.add_argument("--until", required=True, help="window end, ISO UTC")
    parser.add_argument("--room", help="channel id: also run the platform half (needs OKCHEESE_TOKEN)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--cache", help="directory to cache gh answers in (speeds up re-runs)")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    global CACHE_DIR
    CACHE_DIR = args.cache
    since, until = _iso(args.since), _iso(args.until)
    report = build(args.repo, since, until, args.room)
    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2, default=str)
        print()
    else:
        print(render_markdown(report))
    return 0


def self_test() -> int:
    """Offline checks of the pure parts: no network, no gh, no token."""
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    def rnd(head, *outcomes, tier="full"):
        return {
            "sha": head,
            "rounds": [
                {"outcome": o, "tier": tier, "started_at": f"2026-10-12T11:00:0{i}", "jobs": []}
                for i, o in enumerate(outcomes)
            ],
        }

    # ci_rounds: two jobs of one run collapse; a later run stays separate.
    checks = [
        {"name": REQUIRED_FULL, "conclusion": None, "started_at": "2026-10-12T11:00:00Z", "completed_at": ""},
        {
            "name": REQUIRED_DRAFT,
            "conclusion": "failure",
            "started_at": "2026-10-12T11:00:02Z",
            "completed_at": "",
        },
        {
            "name": REQUIRED_DRAFT,
            "conclusion": None,
            "started_at": "2026-10-12T11:30:00Z",
            "completed_at": "",
        },
        {
            "name": REQUIRED_FULL,
            "conclusion": "success",
            "started_at": "2026-10-12T11:30:01Z",
            "completed_at": "",
        },
    ]
    rounds = ci_rounds(checks)
    check("draft-fail then full-green is two rounds", [r["outcome"] for r in rounds], ["red", "green"])
    check(
        "all-skipped round is dropped",
        ci_rounds(
            [
                {
                    "name": REQUIRED_FULL,
                    "conclusion": "skipped",
                    "started_at": "2026-10-12T11:00:00Z",
                    "completed_at": "",
                }
            ]
        ),
        [],
    )

    # heads_of: committed + force-push, deduped, in order.
    tl = [
        {"event": "committed", "sha": "a"},
        {"event": "committed", "sha": "b"},
        {"event": "head_ref_force_pushed", "commit_id": "c"},
        {"event": "committed", "sha": "b"},
    ]
    check("head history dedupes and keeps order", heads_of(tl), ["a", "b", "c"])

    # strict vs lenient on the same three shapes.
    check("strict: green first push passes", strict_pass(rnd("a", "green")), True)
    check("strict: cancelled first push fails", strict_pass(rnd("a", "cancelled", "green")), False)
    check(
        "lenient: cancelled then green on the same head passes",
        _lenient(rnd("a", "cancelled", "green")["rounds"]),
        True,
    )
    check(
        "lenient: an only-cancelled first head stays a miss",
        _lenient(rnd("a", "cancelled")["rounds"]),
        False,
    )
    check(
        "lenient: a full-tier red first verdict still fails",
        _lenient(rnd("a", "red", "green")["rounds"]),
        False,
    )
    draft_red_then_full_green = [
        {"outcome": "red", "tier": "draft", "started_at": "2026-10-12T11:44:14", "jobs": []},
        {"outcome": "green", "tier": "full", "started_at": "2026-10-12T11:57:49", "jobs": []},
    ]
    check(
        "lenient: a draft-tier red before the full green passes (#3496 shape)",
        _lenient(draft_red_then_full_green),
        True,
    )
    # pr_report: only the heads CI ran on are pushes. `committed` lists a
    # multi-commit push once per commit and the non-tip ones ran no CI (#3493 /
    # #3487 shape), so one push of two commits is one round, and 首推 judges the
    # head the required check actually saw — not `heads[0]`.
    def pr_row(number: int) -> dict:
        return {"number": number, "title": "t", "mergedAt": "x", "mergeCommit": {"oid": "m"}}

    rep = pr_report(pr_row(1), [rnd("a"), rnd("b", "cancelled", "green")], [])
    check("首推 names the head CI ran on", rep["first_head"], "b")
    check("a non-tip commit is not a push", rep["pushes"], 1)
    check("首推 宽松 reads that head", rep["lenient_first_pass"], True)
    check(
        "a later head's green does not rescue the first head",
        pr_report(pr_row(2), [rnd("a", "cancelled"), rnd("b", "green")], [])["lenient_first_pass"],
        False,
    )
    check(
        "a PR whose heads all ran no CI has no first head",
        pr_report(pr_row(3), [rnd("a"), rnd("b")], [])["first_head"],
        None,
    )
    # 严格 on its own: judged against `heads[0]` — the commit CI never ran —
    # this comes out False, so the True here pins the head it read.
    check(
        "首推 严格 reads that head too",
        pr_report(pr_row(4), [rnd("a"), rnd("b", "green")], [])["strict_first_pass"],
        True,
    )
    check(
        "and that head's cancelled first round still counts against it",
        rep["strict_first_pass"],
        False,
    )

    # green_of: the boundary is a full-tier green, not a draft one.
    draft_green = {"outcome": "green", "tier": "draft", "started_at": "2026-10-12T11:00:00"}
    check(
        "a draft-tier green is not the boundary",
        green_of({"push_detail": [{"sha": "a", "rounds": [draft_green]}]}),
        None,
    )
    check(
        "the later full-tier green is",
        green_of(
            {
                "push_detail": [
                    {
                        "sha": "a",
                        "rounds": [draft_green, {"outcome": "green", "tier": "full", "completed_at": "2026-10-12T11:30:00"}],
                    }
                ]
            }
        ),
        "2026-10-12T11:30:00",
    )

    # merge_queue_exits: only removals inside the window are this window's.
    # `runs=[]` keeps both calls off the network — no entry, so no job lookup.
    window = (_iso("2026-10-12T11:00:00Z"), _iso("2026-10-12T14:05:00Z"))
    before = [{"event": "removed_from_merge_queue", "created_at": "2026-10-12T10:00:00Z"}]
    inside = [{"event": "removed_from_merge_queue", "created_at": "2026-10-12T12:00:00Z"}]
    check("a removal before the window is not this window's", merge_queue_exits(before, [], "r", *window), [])
    check(
        "a removal inside the window counts",
        [e["at"] for e in merge_queue_exits(inside, [], "r", *window)],
        ["2026-10-12T12:00:00Z"],
    )

    # first_error: earliest failing job by completion time, not the aggregate.
    jobs = [
        {
            "name": "CI required",
            "conclusion": "failure",
            "started_at": "x",
            "completed_at": "2026-10-12T12:26:03Z",
        },
        {
            "name": "backend / integration (3/5)",
            "conclusion": "failure",
            "started_at": "x",
            "completed_at": "2026-10-12T12:23:10Z",
        },
        {
            "name": "backend / static",
            "conclusion": "success",
            "started_at": "x",
            "completed_at": "2026-10-12T12:19:12Z",
        },
    ]
    check("first error is the earliest failing job", first_error(jobs), "backend / integration (3/5)")
    check(
        "a green run has no first error", first_error([j for j in jobs if j["conclusion"] == "success"]), None
    )

    # _minutes and the None discipline.
    check("minutes between two stamps", _minutes("2026-10-12T11:00:00", "2026-10-12T11:15:12"), 15.2)
    check("a missing end is None, not 0", _minutes("2026-10-12T11:00:00", None), None)
    check("median of even list averages", _median([10.0, 20.0]), 15.0)

    # turns_by_seat: 未完成 vs 真失败 vs 断流 are three different questions.
    seats = turns_by_seat(
        [
            {"seat": "s1", "code": "response_truncated", "event_type": "platform_error", "task": 1},
            {"seat": "s1", "code": "response_truncated", "event_type": "platform_error", "task": 2},
            {"seat": "s1", "code": "turn_timeout", "event_type": "turn_timeout", "task": 1},
            {"seat": "s2", "code": "unclassified", "event_type": "turn_failed", "task": 3},
        ]
    )
    check("seats group by reason", seats["s1"]["by_code"], {"断流": 2, "turn_timeout": 1})
    check("seats list their tasks", seats["s1"]["tasks"], [1, 2])
    check("未完成 counts every unfinished event", seats["s1"]["total"], 3)
    check("真失败 counts only turn_failed/platform_error", seats["s1"]["broken"], 2)
    check("断流 counts cut streams", seats["s1"]["truncated"], 2)
    check("a timeout with no code is not 断流", seats["s2"]["truncated"], 0)

    # lines_table: a task with no card in the window is not a 线 that moved.
    lt = lines_table(
        [{"number": 1445, "seat": "s", "status": "open"}, {"number": 1499, "seat": "s", "status": "open"}],
        [{"task": 1445, "seat": "s", "status": "accepted", "pr_number": 3495}],
        [{"number": 3495}],
    )
    check("only touched lines appear", [r["task"] for r in lt], [1445])
    check("a merged pr is named", lt[0]["merged_prs"], [3495])

    # _gh: a failed call is not data. `gh` prints the API's error body — itself
    # JSON — on stdout, so an exit code nobody reads turns a rate-limited call
    # into an empty answer, and `--cache` freezes that answer on disk. `gh` is
    # faked here, so this stays offline.
    import tempfile

    global CACHE_DIR

    real_run, real_cache = subprocess.run, CACHE_DIR
    calls = []

    class FailedProc:
        returncode = 1
        stdout = '{"message":"API rate limit exceeded","status":"403"}\n'
        stderr = "gh: API rate limit exceeded (HTTP 403)"

    def failed_run(cmd, **_kw):
        calls.append(cmd)
        return FailedProc()

    cache = tempfile.mkdtemp(prefix="gh-fail-")
    subprocess.run, CACHE_DIR = failed_run, cache
    try:
        try:
            _gh(["api", "rate_limit"], attempts=2)
            check("_gh raises on a failed gh call", "returned a value instead", "GhError")
        except GhError:
            pass
    finally:
        subprocess.run, CACHE_DIR = real_run, real_cache
    check("a failed call is retried", len(calls), 2)
    check("a failed call caches nothing", os.listdir(cache), [])

    for f in failures:
        print("FAIL", f)
    print("self-test:", "ok" if not failures else f"{len(failures)} failed")
    return 1 if failures else 0


def strict_pass(push: dict) -> bool:
    """The 严格 reading, named so the self-test and pr_reports agree on it: the
    FIRST head's first round went green (a cancelled or red first round fails).
    `_lenient` above is the other reading, and the two are deliberately kept as
    separate functions rather than one function with a mode."""
    rounds = push["rounds"]
    return bool(rounds) and rounds[0]["outcome"] == "green"


def main() -> int:
    return retro_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
