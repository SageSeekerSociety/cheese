#!/usr/bin/env python3
"""The orchestrator's patrol sweep: list what needs a decision across a channel's task lines.

It reads the platform and GitHub and writes nothing, except under --sync-doc, which
appends the rendered board to one document and never restates anything above it.

Usage (keep the password in the command's heredoc, never in a file):
  OKCHEESE_USER=... OKCHEESE_PASS=... python3 .claude/scripts/orchestrator_sweep.py <channel topic id> \
      [--silent-min 60] [--skip 1443,1405] [--json | --render | --sync-doc <document id>]

For every open, started task in the channel it reads platform facts and the PR's GitHub queue state:
  出队          card pending, PR neither merged nor queued, last queue event is a removal
  可采纳未采纳  card pending, PR mergeable >= 5 min, not queued, not removed since
  红了没人修    CI red / conflict / post-merge checks failed >= 30 min ago, no message since
  连续失败      last two system events are failed turns, no message since
  长时间无动静  last message older than --silent-min minutes
Each row carries waiting_on; --skip leaves out tasks deliberately on hold.

The default output is that text list, unchanged. The same sweep in other shapes:
  --json       every class's rows — task, topic, seat, PR, head, CI state, the head its
               review is pinned to, minutes waited, whom it waits on, the PR's two core
               sections and its failed checks and cases — plus that UTC day's metrics.
               Fixed shape, for tools.
  --render     the same data as Markdown, one line per signal, for a person to read;
               under the table, one block per task with what its PR changed, how it was
               verified, and (when it is red) which checks are red and which cases the
               logs name. A section the description never wrote says so rather than
               being filled in.
  --sync-doc D write the rendered board into document D between <!-- board:start --> and
               <!-- board:end -->; when the markers are not there yet, append a marked
               section by naming a trailing passage and putting the board after it, and
               refuse when no such passage can be named. One of these three, not two at
               once.
"""
import json, os, re, statistics, subprocess, sys, urllib.error, urllib.request
from datetime import datetime, timezone

API = os.environ.get("OKCHEESE_API", "https://okcheese.com/api")
REPO = os.environ.get("ORCHESTRATOR_REPO", "SageSeekerSociety/cheese").split("/")

#: The classes a row carries, in the order the text list prints them. "GitHub 未查"
#: is not a decision to make — it is the sweep saying it could not look.
CLASSES = ["出队", "可采纳未采纳", "红了没人修", "连续失败", "长时间无动静", "GitHub 未查"]
BOARD_START, BOARD_END = "<!-- board:start -->", "<!-- board:end -->"
#: The first line of the comment pr-review.sh posts on a PR, naming the head it reviewed.
REVIEW_MARK = re.compile(r"<!--\s*pr-review sha=([0-9a-f]{7,40})")
CI_WORD = {"SUCCESS": "绿", "FAILURE": "红", "ERROR": "红", "PENDING": "跑", "EXPECTED": "跑"}
ROW_FIELDS = ("task", "title", "topic", "seat", "pr", "head", "ci",
              "review_head", "waited_min", "waiting_on", "stage", "detail",
              "changed", "verified", "checks", "cases")
#: PR -> its merge-queue timeline, filled once per run (see gh_queue_timeline).
_TIMELINES = {}
#: PRs this run could not read a timeline for at all. The day's numbers are short by those,
#: so _TIMELINE_ERRORS carries why and the metrics line says so rather than leaving a gap.
_UNREAD = set()
_TIMELINE_ERRORS = []


def login():
    body = json.dumps({"username": os.environ["OKCHEESE_USER"],
                       "password": os.environ["OKCHEESE_PASS"]}).encode()
    req = urllib.request.Request(API + "/users/auth/login", data=body,
                                 headers={"content-type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))["data"]["accessToken"]


def get(tok, path):
    req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + tok})
    return json.load(urllib.request.urlopen(req, timeout=120))["data"]


def send(tok, method, path, body):
    """A write to the platform. A refusal (409 stale, 422 the passage is not there)
    is the platform doing its job, so it is reported as such and the run stops."""
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), method=method,
                                 headers={"content-type": "application/json",
                                          "Authorization": "Bearer " + tok})
    try:
        return json.load(urllib.request.urlopen(req, timeout=120)).get("data")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} 被平台拒了：{e.code} "
                         + e.read(400).decode("utf-8", "replace"))


def events(tok, room, author, pages=50, since=None):
    """Channel-wide events (channel=true), paged until exhausted or older than `since`."""
    out, before = [], None
    for _ in range(pages):
        d = get(tok, f"/topics/{room}/history?channel=true&author={author}&limit=200"
                     + (f"&before={before}" if before else ""))
        out += d["data"]
        if not d.get("has_more") or (since and d["data"] and min(m["created_at"] for m in d["data"]) < since):
            break
        before = d["oldest_id"]
    return out


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def meta(m):
    return m.get("meta") if isinstance(m.get("meta"), dict) else {}


def said(m):
    try:
        return str(meta(m)["i18n"]["detail"]["params"].get("said", ""))[:100]
    except Exception:
        return ""


def gh_pr(n, board=False):
    """One PR's queue state. `board` adds what only the board shows: the head, that
    head's CI state, that head's per-check verdicts, the description, and the head the
    newest review is pinned to. Read-only, one call.

    Those fields stay out of the text list's query — it asks for exactly what it
    always asked for, so a field the board wants can never be the reason the text
    list reports "GitHub 未查". The per-check verdicts are what the board reports a
    red by: the names of the ones that did not pass, in GitHub's own order, the first
    50 of them."""
    extra = ('headRefOid body commits(last:1){nodes{commit{statusCheckRollup{state '
             'contexts(first:50){nodes{__typename '
             '... on CheckRun{name conclusion detailsUrl} '
             '... on StatusContext{context state targetUrl}}}}}}} '
             'comments(last:100){nodes{body}} ') if board else ''
    q = ('query($n:Int!){repository(owner:"%s",name:"%s"){pullRequest(number:$n){merged '
         + extra
         + 'mergeQueueEntry{state} '
           'timelineItems(last:3,itemTypes:[ADDED_TO_MERGE_QUEUE_EVENT,REMOVED_FROM_MERGE_QUEUE_EVENT])'
           '{nodes{__typename ... on AddedToMergeQueueEvent{createdAt} '
           '... on RemovedFromMergeQueueEvent{createdAt reason}}}}}}') % tuple(REPO)
    err = ""
    for _ in range(3):
        r = subprocess.run(["gh", "api", "graphql", "-f", "query=" + q, "-F", f"n={n}"],
                           capture_output=True, text=True)
        try:
            body = json.loads(r.stdout)["data"]["repository"]["pullRequest"]
        except (ValueError, KeyError, TypeError):
            err = (r.stderr or r.stdout)[:150]
            continue
        # 查不到（删了、编号不属于这个仓库）时 GitHub 回的是格式正确、pullRequest 为
        # null 的响应。它得走 "error" 那条路，不然调用方 `if "error" in pr` 直接
        # TypeError，整趟巡检断在这里。
        if body is None:
            return {"error": f"GitHub 上没有 PR #{n}"}
        commits = (body.get("commits") or {}).get("nodes") or []
        rollup = ((commits[0]["commit"].get("statusCheckRollup") if commits else None) or {})
        pinned = None
        for c in reversed((body.get("comments") or {}).get("nodes") or []):
            found = REVIEW_MARK.search(c.get("body") or "")
            if found:
                pinned = found.group(1)
                break
        body["head"] = body.get("headRefOid")
        body["ci"] = rollup.get("state")
        body["review_head"] = pinned
        body["desc"] = body.pop("body", None) or ""
        body["checks"] = failed_checks(rollup)
        return body
    return {"error": err}


#: The verdicts that mean a check did not pass. ACTION_REQUIRED is not something the
#: code did, but it is not a pass either, so it is reported like the rest.
_FAILED_VERDICT = frozenset({"FAILURE", "ERROR", "TIMED_OUT", "STARTUP_FAILURE",
                             "ACTION_REQUIRED"})


def failed_checks(rollup):
    """The checks that did not pass, each as {name, url} — the name the board prints and
    the page a log can be read from. Names only: what they mean is the reader's call,
    and this file does not make it."""
    out = []
    for node in ((rollup.get("contexts") or {}).get("nodes") or []):
        if node.get("__typename") == "CheckRun":
            if node.get("conclusion") in _FAILED_VERDICT:
                out.append({"name": node.get("name") or "?", "url": node.get("detailsUrl") or ""})
        elif str(node.get("state") or "").upper() in ("FAILURE", "ERROR"):
            out.append({"name": node.get("context") or "?", "url": node.get("targetUrl") or ""})
    return out


def gh_queue_timeline(n):
    """A PR's merge-queue timeline (first 50 events). None when GitHub has no such PR, and
    also when the read itself failed after its retries: the board is the point of a patrol
    round, so one unreadable PR costs that PR's samples rather than the whole board. Which
    ones were unreadable is left in _TIMELINE_ERRORS for the metrics line to own up to.

    What is remembered is per run and answers for every event naming that PR: a day's events
    can name the same PR a dozen times, and neither a read that worked nor one that failed is
    worth repeating. A PR GitHub says is not there is remembered by neither, as in
    orchestrator_metrics.py — that answer is about the number, not about GitHub."""
    if n in _TIMELINES:
        return _TIMELINES[n]
    if n in _UNREAD:
        return None
    q = ('query($n:Int!){repository(owner:"%s",name:"%s"){pullRequest(number:$n){'
         'timelineItems(first:50,itemTypes:[ADDED_TO_MERGE_QUEUE_EVENT,REMOVED_FROM_MERGE_QUEUE_EVENT])'
         '{nodes{__typename ... on AddedToMergeQueueEvent{createdAt} '
         '... on RemovedFromMergeQueueEvent{createdAt reason}}}}}}' % tuple(REPO))
    err = ""
    for _ in range(3):
        r = subprocess.run(["gh", "api", "graphql", "-f", "query=" + q, "-F", f"n={n}"],
                           capture_output=True, text=True)
        try:
            body = json.loads(r.stdout)["data"]["repository"]["pullRequest"]
        except (ValueError, KeyError, TypeError):
            err = (r.stderr or r.stdout)[:150]
            continue
        nodes = None if body is None else body["timelineItems"]["nodes"]
        if nodes is not None:
            _TIMELINES[n] = nodes
        return nodes
    _UNREAD.add(n)
    # 一行一句：gh 的 stderr 常带换行，原样塞进指标那行会把看板拆成两行。
    _TIMELINE_ERRORS.append(f"#{n}（{' '.join(err.split()) or '查不到原因'}）")
    return None


#: The PR-body headings a reader judges by: what the change is, and how it was checked.
#: The template asks for `## What changed` and `## How it was verified`, but most bodies
#: here carry neither — so the opening paragraphs answer for "what changed" when no
#: heading claims them, and a plain `验证：` line answers for the other half.
_SECTION_WORDS = {
    "changed": ("what chang", "changes", "改动", "改了什么"),
    "verified": ("verif", "valid", "test", "验证", "怎么验证"),
}
#: A line that labels the part after it — `**验证**：…`, `验证：`, `## Tests` alike. How
#: this repo actually writes the second half, far more often than as a heading.
_LABEL = re.compile(r"^[ \t]*(?:\*\*)?([^\n：:*]{1,20})(?:\*\*)?[ \t]*[：:][ \t]*", re.M)
_VERIFIED_LABEL = ("验证", "怎么验证", "测试", "自测", "verif", "valid", "test", "check")
_HEADING = re.compile(r"^(#{2,4})\s+(.+?)\s*$", re.M)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
#: Everything from the first of these lines down is the platform's, not the author's.
_TRAILER_LINE = re.compile(r"^(?:Requested-by|Cheese-[A-Za-z-]+|Co-authored-by"
                           r"|Reviewed-by|Reported-by):", re.M)
_AGENT_MARK = re.compile(r"^[ \t]*AGENT:[ \t]*$", re.M)
#: The lines a runner prints to name a case that failed — pytest's summary, the
#: `##[error]` line this repo's own harness adds around it, go test, vitest/jest.
#: The sweep takes the case name out of whichever one is there and says nothing else.
_CASE_LINE = re.compile(
    r"^(?:FAILED|ERROR)\s+(\S+)"
    r"|^##\[error\](\S+::\S+)"
    r"|^--- FAIL:\s*(\w+)"
    r"|^[✕×✗●]\s+(\S.*)$")
#: GitHub stamps every log line with an RFC3339 time and a space.
_LOG_STAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z\s?")
#: A failed check's page names the Actions job whose log carries the case names.
_JOB_IN_URL = re.compile(r"/actions/runs/\d+/job/(\d+)")
_CASES_MAX = 6


def brief(desc, limit=400):
    """The description's 改了什么 / 怎么验证的, each cut to `limit` characters. An empty
    string means the description does not carry that section — the board says so
    instead of filling it in.

    Each half is read off the heading that names it; where no heading does, the
    opening paragraphs answer for "what changed" and a plain label line answers for
    the other half, which is how most bodies here are written."""
    text = _HTML_COMMENT.sub("", desc or "")
    trailer = _TRAILER_LINE.search(text)
    if trailer:
        text = text[:trailer.start()]
    agent = _AGENT_MARK.search(text)
    if agent:
        text = text[agent.end():]
    text = text.strip()
    if not text:
        return "", ""
    heads = list(_HEADING.finditer(text))
    all_labels = list(_LABEL.finditer(text))
    ver = [m for m in all_labels
           if any(w in m.group(1).lower() for w in _VERIFIED_LABEL)]
    verified = _under_heading(text, heads, "verified") or _under_label(text, ver, all_labels)
    changed = _under_heading(text, heads, "changed")
    if not changed:
        # 没有标题说「改了什么」时，正文开头到「验证」之前的那一段就是它 —— 前面是
        # 背景，后面是验证，中间剩下的全是改动。
        stops = [h.start() for h in heads] + [m.start() for m in ver] + [len(text)]
        changed = text[:min(stops)].strip()
    return cut(changed, limit), cut(verified, limit)


def _under_heading(text, heads, want):
    """The body of the first heading that names `want`, or "" when there is none."""
    for i, h in enumerate(heads):
        if any(w in h.group(2).lower() for w in _SECTION_WORDS[want]):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            return text[h.end():end].strip()
    return ""


def _under_label(text, hits, labels):
    """The first hit's body, stopping at the next labelled line of any kind."""
    if not hits:
        return ""
    after = [m.start() for m in labels if m.start() > hits[0].start()]
    return text[hits[0].end():min(after) if after else len(text)].strip()


def cut(text, limit):
    """One section as the board prints it: runs of blank lines squeezed out, and the
    whole thing cut to `limit` characters, saying so on the end when it was."""
    lines = []
    for ln in text.split("\n"):
        ln = ln.rstrip()
        if ln or (lines and lines[-1]):
            lines.append(ln)
    joined = "\n".join(lines).strip()
    return joined if len(joined) <= limit else joined[:limit].rstrip() + " …（截断）"


def failing_cases(checks, jobs=2):
    """The case names the failed jobs print, read out of their logs and quoted as they
    are — up to `_CASES_MAX` of them, and nothing about why they failed. A job whose
    log cannot be read contributes nothing rather than stopping the sweep."""
    out = []
    for check in [c for c in checks if c.get("url")][:jobs]:
        job = _JOB_IN_URL.search(check["url"])
        if not job:
            continue
        r = subprocess.run(["gh", "api", f"repos/{REPO[0]}/{REPO[1]}/actions/jobs/"
                                         f"{job.group(1)}/logs"],
                           capture_output=True, text=True)
        for raw in (r.stdout or "").splitlines()[-6000:]:
            found = _CASE_LINE.match(_LOG_STAMP.sub("", raw).strip())
            if not found:
                continue
            name = next((g for g in found.groups() if g), "").strip()[:200]
            if name and name not in out:
                out.append(name)
    return out[:_CASES_MAX]


def stage_of(card, pr, ready_at, last_ready, red_at, last_msg):
    """到哪一步了 — a phrase from facts already read, nothing invented. Finer than
    this (实现中、在重钉) is not a field the platform has; it is in the line's last message.
    `ready_at` is the card saying "可以合并" and nothing having replaced it since;
    `last_ready` is the last time it said so, which is what a queue removal is dated against."""
    if not card or not card.get("pr_number"):
        return "在做未递卡"
    if card.get("status") == "accepted":
        return "已合并" if pr and pr.get("merged") else "已采纳等落主干"
    if pr is None:
        return "递卡后等 CI 审查"
    if pr.get("error"):
        return "GitHub 查不到"
    if pr.get("merged"):
        return "已合并"
    if pr.get("mergeQueueEntry"):
        return "在合并队列"
    tl = pr["timelineItems"]["nodes"]
    if tl and tl[-1]["__typename"] == "RemovedFromMergeQueueEvent" and (not last_ready or tl[-1]["createdAt"] > last_ready):
        return "出队待修"
    if ready_at:
        return "可采纳未采纳"
    if red_at and red_at > last_msg:
        return "红了待修"
    return "递卡后等 CI 审查"


def waiting_of(t, kind):
    """在等谁 — the platform's own field when it is set, otherwise the party the
    class implies: an adoptable card waits on the reviewer, everything else on the line."""
    if t.get("waiting_on"):
        return t["waiting_on"]
    if kind == "可采纳未采纳":
        return t.get("reviewer_handle") or t.get("owner_handle")
    return t.get("agent_handle")


def row(kind, name, text, t, card, pr, waited, waiting, ready_at, last_ready, red_at, last_msg):
    good = pr is not None and "error" not in pr
    changed, verified = brief(pr.get("desc")) if good else ("", "")
    return {
        "kind": kind, "name": name, "task": t["number"], "title": t["title"], "topic": t["id"],
        "seat": t.get("agent_handle"),
        "pr": card.get("pr_number"),
        "head": pr.get("head") if good else None,
        "ci": CI_WORD.get(pr.get("ci"), "无") if good else None,
        "review_head": pr.get("review_head") if good else None,
        "waited_min": round(waited) if waited is not None else None,
        "waiting_on": waiting or waiting_of(t, kind),
        "stage": stage_of(card, pr, ready_at, last_ready, red_at, last_msg),
        "detail": text,
        "changed": changed,
        "verified": verified,
        "checks": [c["name"] for c in (pr.get("checks") or [])] if good else [],
        "cases": (pr.get("cases") or []) if good else [],
    }


def sweep(tok, room, silent, skip, want_facts):
    """The rows the text list prints, each also carrying the board's fields. GitHub
    facts are read only for tasks that have a PR: for the pending ones always, and for
    the rest only when `want_facts`, which is set by the flags that show the board — so
    an invocation with no flag makes exactly the calls it always made."""
    now = datetime.now(timezone.utc)
    age = lambda s: (now - ts(s)).total_seconds() / 60
    d = get(tok, f"/topics/{room}/tasks?status=open&limit=200")
    tasks = d.get("data", d) if isinstance(d, dict) else d
    rows, facts, idmap = [], {}, {}
    for t in tasks:
        if not t.get("started_at") or str(t["number"]) in skip:
            continue
        tid = t["id"]
        idmap[tid] = t["number"]
        name = f"#{t['number']} {t['title'][:28]} @{t.get('agent_handle') or '-'}" + (f" 等:{t['waiting_on']}" if t.get("waiting_on") else "")
        msg = get(tok, f"/topics/{tid}/history?limit=1&kind=message")["data"]
        last_msg = msg[-1]["created_at"] if msg else t["started_at"]
        sysev = get(tok, f"/topics/{tid}/history?limit=30&author=system")["data"]
        accev = get(tok, f"/topics/{tid}/history?limit=30&author=accept")["data"]
        sysev.sort(key=lambda m: m["created_at"])
        accev.sort(key=lambda m: m["created_at"])

        card = t.get("card") or {}
        n = card.get("pr_number")
        pr = None
        if n and (card.get("status") == "pending" or want_facts):
            # 文本清单照老样子一卡一查，失败的那次也留到下一张卡自己重试；看板模式
            # 同一个 PR 只查一次，多出来的字段没必要读两遍。
            if want_facts and n in facts:
                pr = facts[n]
            else:
                pr = gh_pr(n, board=want_facts)
                if want_facts:
                    facts[n] = pr

        ready = [m for m in accev if meta(m).get("event_type") == "accept_ready"]
        last_ready = ready[-1]["created_at"] if ready else None
        # 卡还在「可以合并」上，得是最后一条 accept 事件就是 accept_ready。中间插进来
        # 的改审阅说明（card_redescribed）等于是把这份结论换掉了，不能再当可采纳。
        ready_at = accev[-1]["created_at"] if accev and meta(accev[-1]).get("event_type") == "accept_ready" else None
        red = [m for m in sysev if meta(m).get("event_type") in ("ci_failed", "pr_conflict", "main_checks_failed")]
        red_at = red[-1]["created_at"] if red else None
        tail = [m for m in sysev if meta(m).get("event_type")]

        def add(kind, text, waited=None, waiting=None):
            rows.append(row(kind, name, text, t, card, pr, waited, waiting, ready_at, last_ready, red_at, last_msg))

        if card.get("status") == "pending" and n:
            if "error" in pr:
                add("GitHub 未查", f"PR #{n}：{pr['error']}")
            elif not pr["merged"] and not pr["mergeQueueEntry"]:
                tl = pr["timelineItems"]["nodes"]
                if tl and tl[-1]["__typename"] == "RemovedFromMergeQueueEvent" and (not last_ready or tl[-1]["createdAt"] > last_ready):
                    w = age(tl[-1]["createdAt"])
                    add("出队", f"PR #{n} {tl[-1]['createdAt'][11:16]} UTC 出队（{tl[-1]['reason']}），{w:.0f} 分钟没回队列", w)
                elif ready_at and age(ready_at) >= 5:
                    w = age(ready_at)
                    add("可采纳未采纳", f"PR #{n} 可以合并 {w:.0f} 分钟", w)

        if red and red[-1]["created_at"] > last_msg and age(red[-1]["created_at"]) >= 30:
            w = age(red[-1]["created_at"])
            # 红在哪只进看板：文本清单照老样子一卡一查，看板的模式才去读每个检查的
            # 判定，读日志拿用例名更贵，所以只在真的要摆出来的时候读。
            where = ""
            if want_facts and pr is not None and "error" not in pr:
                if pr.get("cases") is None:
                    pr["cases"] = failing_cases(pr.get("checks") or [])
                names = [c["name"] for c in (pr.get("checks") or [])]
                where = "，红的是 " + "、".join(names) if names else ""
            add("红了没人修", f"{red[-1]['content'][:50]}{where}，{w:.0f} 分钟线上没新消息", w)

        if len(tail) >= 2 and all(meta(m)["event_type"] == "turn_failed" for m in tail[-2:]) and tail[-1]["created_at"] > last_msg:
            w = age(tail[-1]["created_at"])
            add("连续失败", f"最后 {tail[-1]['created_at'][11:16]} UTC：{said(tail[-1])}", w)

        if age(last_msg) >= silent:
            w = age(last_msg)
            add("长时间无动静", f"{w:.0f} 分钟没消息", w)

    rows.sort(key=lambda r: CLASSES.index(r["kind"]))
    return rows, idmap


def text(rows):
    if not rows:
        print("各线无待处理信号")
        return
    for r in rows:
        print(f"[{r['kind']}] {r['name']}｜{r['detail']}")


def render(rows, metrics, silent, now, room):
    out = ["## 调度看板", "",
           f"UTC {now:%Y-%m-%d %H:%M}｜信号阈值 {silent} 分钟｜{len(rows)} 条｜频道 {room[:8]}", ""]
    if not rows:
        out.append("各线无待处理信号。")
    else:
        out += ["| 类别 | 任务 | 到哪一步 | CI | 审查钉 | 卡了 | 在等 |",
                "| --- | --- | --- | --- | --- | --- | --- |"]
        for r in rows:
            rev = r["review_head"]
            rev = "—" if rev is None else (rev[:8] + (" ✓" if rev == r["head"] else "（旧）"))
            waited = "—" if r["waited_min"] is None else f"{r['waited_min']} 分"
            out.append(f"| {r['kind']} | #{r['task']} | {r['stage']} | {r['ci'] or '—'} "
                       f"| {rev} | {waited} | {r['waiting_on'] or '—'} |")
    out += ["", metrics_line(metrics)]
    if rows:
        out += ["", "### 每个待判 PR", ""] + details(rows)
    return "\n".join(out)


def details(rows):
    """每张待判的卡下面，把它 PR 正文里的那两段、和它红在哪个检查／哪些用例摆出来 ——
    读完这一段就不用再开 PR 页面。同一个任务出现在多个类别里时只说一次。正文没写的
    那截、GitHub 没说红在哪个检查的，都如实说没有，看板不替它们补话。"""
    seen, out = set(), []
    for r in rows:
        if r["task"] in seen:
            continue
        seen.add(r["task"])
        out += [f"**#{r['task']} {r['title']}**｜{r['kind']}｜{r['stage']}", ""]
        for label, text in (("改了什么", r["changed"]), ("怎么验证的", r["verified"])):
            if text:
                out += [f"{label}："] + ["> " + ln if ln.strip() else ">" for ln in text.split("\n")]
            else:
                out.append(f"{label}：PR 正文里没写这一节")
            out.append("")
        if r["checks"]:
            out.append("红的是：" + "、".join(r["checks"]))
        if r["cases"]:
            out += ["失败用例："] + ["> " + ln for ln in r["cases"]]
        if r["checks"] or r["cases"]:
            out.append("")
    return out


def metrics_line(m):
    q, f = m["merge_to_queue"], m["failed_turns"]
    med = "—" if q["median_min"] is None else f"{q['median_min']:.0f}"
    late = "、".join(f"#{n} {w}分" for n, w in q["over_10_min_detail"]) or "无"
    lines = "、".join(f"#{c} {k}" for c, k in list(f["by_line"].items())[:8]) or "无"
    short = m.get("unread_queue") or []
    return (f"指标（{m['date']} UTC）：可合并→进队列 {q['samples']} 次，中位 {med} 分；"
            f"超 10 分钟 {q['over_10_min_cards']} 张卡、{q['over_10_min']} 次（{late}）；"
            f"失败轮次 {f['total']}（{lines}）"
            + (f"；队列时间线没读到 {'、'.join(short)}，上面两项少了它" if short else ""))


def day_metrics(tok, room, day, idmap):
    """That UTC day's numbers, computed the way orchestrator_metrics.py defines them:
    可合并→进队列 is accept_ready to the first AddedToMergeQueueEvent after it (accept_done
    is sent after the merge and is never the acceptance time). Read fresh, stored nowhere.

    A PR whose timeline cannot be read is left out and named under unread_queue; the one-off
    report in orchestrator_metrics.py stops there instead, which is right for a script whose
    whole output is those numbers and wrong for a board that is asked for on a schedule."""
    _UNREAD.clear()
    _TIMELINE_ERRORS.clear()
    evs = [m for m in events(tok, room, "accept", pages=10) + events(tok, room, "system", pages=10)
           if m["created_at"].startswith(day)]
    evs.sort(key=lambda m: m["created_at"])
    by = {}
    for m in evs:
        by.setdefault(m["conversation_id"], []).append(m)
    waits, late, fails = [], [], {}
    for c, ev in by.items():
        for m in ev:
            et = meta(m).get("event_type")
            if et == "turn_failed":
                fails[c] = fails.get(c, 0) + 1
            if et == "accept_ready":
                found = re.search(r"#(\d+)", m["content"] or "")
                if not found:
                    continue
                nodes = gh_queue_timeline(int(found.group(1))) or []
                add = next((x["createdAt"] for x in nodes if x["__typename"] == "AddedToMergeQueueEvent"
                            and x["createdAt"] >= m["created_at"]), None)
                if add:
                    w = round((ts(add) - ts(m["created_at"])).total_seconds() / 60)
                    waits.append(w)
                    if w > 10:
                        late.append((found.group(1), w))
    waits.sort()
    by_line = {str(idmap.get(c, c[:8])): k for c, k in sorted(fails.items(), key=lambda x: -x[1])[:8]}
    return {
        "date": day,
        "merge_to_queue": {
            "samples": len(waits),
            "median_min": statistics.median(waits) if waits else None,
            "p90_min": waits[min(len(waits) - 1, int(len(waits) * 0.9))] if waits else None,
            "max_min": waits[-1] if waits else None,
            # 卡数按 PR 去重：同一张卡退回队列又进去一次，是一条卡等了两次，不是两张卡。
            "over_10_min_cards": len({n for n, _ in late}),
            "over_10_min": len(set(late)),
            "over_10_min_detail": sorted(set(late)),
        },
        "failed_turns": {"total": sum(fails.values()), "by_line": by_line},
        #: Queue-timeline reads that failed, so the numbers above are short by these.
        "unread_queue": list(_TIMELINE_ERRORS),
    }


def board_json(rows, metrics, room, silent, skip, now):
    classes = {k: [{f: r[f] for f in ROW_FIELDS} for r in rows if r["kind"] == k] for k in CLASSES}
    return {"generated_at": now.isoformat(), "room": room, "signal_min": silent,
            "skipped": sorted(skip), "classes": classes, "metrics": metrics}


def occurrences(hay, needle):
    """Every place needle starts in hay, overlaps counted — the count the platform
    refuses on (collab/edit.ts steps with indexOf(needle, at + 1), which is what its
    422 docEditOriginalAmbiguous reports). str.count steps past each match, so a
    passage that repeats a short line counts 1 there and more than 1 here."""
    n, at = 0, hay.find(needle)
    while at >= 0:
        n += 1
        at = hay.find(needle, at + 1)
    return n


def unique_tail(content):
    """The shortest run of trailing lines that occurs exactly once, or None when the
    document has no such run. Appending needs a passage to name that is this text and
    nowhere else; this keeps the passage as small as the document allows. Only when no
    shorter trailing run occurs once does the passage become the whole document — a
    document of five identical lines is one such document — and the board is appended
    after it."""
    lines = content.split("\n")
    for start in range(len(lines) - 1, -1, -1):
        candidate = "\n".join(lines[start:])
        if candidate.strip() and occurrences(content, candidate) == 1:
            return candidate
    return None


def sync_doc(tok, document, board):
    """Put the board into the document, and touch nothing else. The document is read
    first. What is submitted to be replaced (POST /documents/{id}/edits) is the marked
    span when the markers are there, and otherwise a trailing passage that occurs once.
    The platform applies the edit only while that passage occurs exactly once in the
    document as it stands then, and `new` keeps that passage as its first characters, so
    every line above it survives verbatim whether or not the passage names it. A passage
    that moved or was duplicated under us is refused rather than guessed at, and a change
    elsewhere in the document is neither a conflict nor touched.

    An empty document takes the whole board (there is nothing to preserve). A marker
    pair that is not exactly one ordered pair is refused here rather than guessed at."""
    try:
        d = get(tok, f"/documents/{document}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"读不到文档 {document}：{e.code}")
    version = (d or {}).get("doc_version", 0)
    content = (d or {}).get("content") or ""
    block = f"{BOARD_START}\n\n{board.strip()}\n\n{BOARD_END}"
    if not content:
        send(tok, "PUT", f"/documents/{document}", {"content": block, "expected_version": version})
        return f"看板已写入 {document}（原文档为空，首版）"
    marks = (content.count(BOARD_START), content.count(BOARD_END))
    if marks == (1, 1) and content.index(BOARD_START) < content.index(BOARD_END):
        i = content.index(BOARD_START)
        j = content.index(BOARD_END) + len(BOARD_END)
        old, new = content[i:j], block
    elif marks == (0, 0):
        old = unique_tail(content)
        if old is None:
            raise SystemExit(f"文档 {document} 里没有一段只出现一次的结尾，加不了看板，先手工放一对 board 标记")
        new = f"{old}\n\n{block}"
    else:
        raise SystemExit(f"文档 {document} 里的 board 标记不是恰好一对"
                         f"（board:start {marks[0]} 次、board:end {marks[1]} 次），不写")
    if occurrences(content, old) != 1:
        raise SystemExit(f"文档 {document} 里要换的原文出现了 {occurrences(content, old)} 次，不写（读到的是 v{version}）")
    send(tok, "POST", f"/documents/{document}/edits",
         {"edits": [{"old": old, "new": new}], "mode": "direct"})
    return f"看板已同步进 {document}（读到的版本 v{version}）"


def value_of(argv, flag):
    """The value after a flag, or a plain error instead of an IndexError when the flag
    was the last word on the line."""
    if flag not in argv:
        return None
    i = argv.index(flag) + 1
    if i >= len(argv) or argv[i].startswith("--"):
        raise SystemExit(f"{flag} 后面要给一个值")
    return argv[i]


def main():
    argv = sys.argv
    room = argv[1]
    silent = int(value_of(argv, "--silent-min") or 60)
    skip = set((value_of(argv, "--skip") or "").split(",")) - {""}
    as_json = "--json" in argv
    as_render = "--render" in argv
    document = value_of(argv, "--sync-doc")
    given = [name for name, on in (("--json", as_json), ("--render", as_render),
                                   ("--sync-doc", document is not None)) if on]
    if len(given) > 1:
        raise SystemExit("、".join(given) + " 只能给一个")
    # `--sync-doc "$VAR"` with an empty VAR arrives as an id of "": counting it as the flag
    # itself (not as absent) is what makes it say so, instead of quietly never syncing.
    if document is not None and not document.strip():
        raise SystemExit("--sync-doc 后面要给一个文档号")
    tok = login()
    now = datetime.now(timezone.utc)
    rows, idmap = sweep(tok, room, silent, skip, as_json or as_render or bool(document))
    if as_json:
        metrics = day_metrics(tok, room, now.date().isoformat(), idmap)
        print(json.dumps(board_json(rows, metrics, room, silent, skip, now), ensure_ascii=False, indent=2))
        return
    if as_render:
        metrics = day_metrics(tok, room, now.date().isoformat(), idmap)
        print(render(rows, metrics, silent, now, room))
        return
    text(rows)
    if document:
        metrics = day_metrics(tok, room, now.date().isoformat(), idmap)
        print(sync_doc(tok, document, render(rows, metrics, silent, now, room)))


if __name__ == "__main__":
    main()
