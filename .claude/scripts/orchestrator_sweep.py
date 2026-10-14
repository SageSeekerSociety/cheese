#!/usr/bin/env python3
"""The orchestrator's patrol sweep: list what needs a decision across a channel's task lines.

It reads the platform and GitHub and writes nothing, except under --sync-doc, which
appends the rendered board to one document and never restates anything above it.

Usage (keep the password in the command's heredoc, never in a file):
  OKCHEESE_USER=... OKCHEESE_PASS=... python3 .claude/scripts/orchestrator_sweep.py <channel topic id> \
      [--silent-min 40] [--skip 1443,1405] [--json | --render | --sync-doc <document id>]
  python3 .claude/scripts/orchestrator_sweep.py --self-test   (offline: no token, no network, no gh)

For every open, started task in the channel it reads platform facts and the PR's GitHub queue state:
  出队          card pending, PR neither merged nor queued, last queue event is a removal
  可采纳未采纳  card pending, PR mergeable >= 5 min, not queued, not removed since
  红了没人修    CI red / conflict / post-merge checks failed >= 30 min ago, no message since
  连续失败      last two system events are failed turns, no message since
  长时间无动静  no heartbeat for --silent-min minutes (default 40, two 20-minute patrol
                rounds). A heartbeat is the newest of the line's last message, the last
                event block its seat wrote (a command it ran) and, when the platform gives
                it, the running turn's last output (`last_output_at` on the task row, which
                moves while one long command streams). A command the seat started and that
                has not returned yet — its event block carries no output yet and no failed
                turn came after it — is a heartbeat now: a seat inside a 45-minute test run
                is working, not stalled. The row says when the last event and the last
                message were and what the message said, so the patrol reads the line's own
                last word before nudging it.
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
              "changed", "verified", "checks", "cases",
              "last_event_at", "last_message_at", "last_message", "last_output_at",
              "running", "heartbeat_min")
#: How much of a line's last message the row carries: enough to tell "审查在跑" from
#: "等总调度", not the whole message.
_MESSAGE_BRIEF = 80
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
#:
#: The words that name the verification half, for a heading (`## Tests`) and for a label
#: line (`**验证**：…`) alike: the same section written two ways, so a word missing from
#: one of them is how `## 测试` came to read as neither half. Both read this base list,
#: and each adds one word of its own to keep from reading the wrong thing: a label line
#: takes `check` (`Checks: …`, `Checked locally: …` are verification and a line is short
#: enough not to wander), a heading takes `checks` and `checked` (`## Checks` is
#: verification, while `## … checklist …` is not — it does not contain `checks`, which is
#: why plain `check` stays out of the headings).
_VERIFIED_WORDS = ("验证", "怎么验", "测试", "自测", "verif", "valid", "test")
_VERIFIED_HEADING_WORDS = _VERIFIED_WORDS + ("checks", "checked")
_VERIFIED_LABEL_WORDS = _VERIFIED_WORDS + ("check",)
#: 每一节按「名字有多直接」分档：第一档是它自己的叫法，第二档也点名了这一节、但没那么
#: 直白。选标题时先看档、再看名字长短 —— 缺了这一层，`## Fixed` 比 `## What changed`
#: 短，就会把「路上顺手修的那几条」当成整份改动摆出来。
#:
#: 改动那一节的第三档只有 `what` / `做了什么`：`## Why` + `## What` + `## Verification`
#: 这种写法里，What 底下就是改动。它排在 Fix 这类之后，是因为 `## What was wrong`、
#: `## What happened` 也以 what 开头，讲的却是出了什么事；同一档里先出现的那个赢，
#: 于是 #2653 的改动段成了一小段死锁栈。只要有一节真的点名了改动，那一节就是改动。
_SECTION_WORDS = {
    "changed": (("what chang", "change", "changes", "改动", "改了什么"),
                ("fix", "behavio", "改法", "修改"),
                ("what", "做了什么")),
    "verified": (_VERIFIED_HEADING_WORDS,),
}
_SECTION_WORDS_FLAT = {k: tuple(w for g in gs for w in g) for k, gs in _SECTION_WORDS.items()}
#: 两半的名字合起来 —— 一行只要提到其中任何一个，就是在给某一节起名字（§ `_names_a_section`）。
_SECTION_NAME_WORDS = _VERIFIED_LABEL_WORDS + _SECTION_WORDS_FLAT["changed"]
#: 标题里说「没改」的那些词：`## What did not change` 讲的是另一半，不是改动本身。
#: 只看开头三个词 —— `## Fix a bug that no longer exists` 是在说改动。
_NEGATING_WORDS = frozenset({"not", "no", "never", "without", "unverified", "none", "nothing"})
_NEGATING_PHRASES = ("没改", "未改", "不改", "无改动", "没有改")
#: A line that labels the part after it — `**验证**：…`, `验证：`, `## Tests` alike. How
#: this repo actually writes the second half, far more often than as a heading.
#: A list item and a URL line start the same way and are not labels: a section whose
#: first line is `- pytest：全过` or a link would otherwise be read as ending right
#: there, and the board would say the body never wrote it.
#: The name is short — a sentence with a colon in it is not a label — but it may carry
#: one bracketed aside of any length after it, because a body that dates or sources its
#: verification writes it there: `验证（跑的时候正在那个窗口里，2026-10-13 00:41 UTC）：`
#: is a label line, and reading it as one only through the first twenty characters would
#: say that body never wrote the section.
_LABEL = re.compile(
    r"^(?![ \t]*[-•*+][ \t])(?![ \t]*\d+[.)][ \t])(?!\w{2,12}://)"
    r"(?:\*\*)?([^\n：:*`，。]{1,40}(?:[（(][^）\n]{0,80}[）)])?)"
    r"(?:\*\*)?[ \t]*[：:](?!//)[ \t]*", re.M)
#: A label line whose name is too long for `_LABEL` to read: `Verification at final SHA
#: 47c36ab320e84053186d5d9e6acdfcfab7d0ee87:` is 66 characters, so the line was not a
#: label, the verification half came out empty, and the board said that body never wrote
#: it. Raising `_LABEL`'s 40 instead reads #3379's opening sentence as a label: its name,
#: `Follow-up to #3378, which did not fix the case it was written for. Checked on dev at
#: 5cccef562`, is 94 characters, and it names a half (`Checked`) — the one way past every
#: check `_is_label` has. The board then took the sentence for the verification half,
#: emptied the change half and handed over the paragraph under it. So the long name gets a
#: pattern of its own and two further conditions: the line is nothing but the name and its
#: colon, and the name says which half it is near its start (§ `_name_head`). A sentence
#: that ends a line on a colon and only mentions the word half a line in — `The dev deploy
#: of 452e49f (run 38037133758) failed at "Release the verified metering image":` — opens on
#: neither half's name and stays out.
#:
#: #3618's wrapped sentence is held back by a different rule, and not the 40: its name
#: (`Independent review at 2c3fb34a`) is 30 characters, inside the 40 already, so raising
#: the 40 never had anything to do with it — measured, at 120 over the 1000 bodies that
#: body reads exactly as it does now, while #3379's change half goes 816 → 0 characters. It
#: names no half, and `_NAME_SHORT` below turns it down on the name's length. A name of 41
#: characters or more has room to say which half it is, which is what the two conditions
#: above read for instead.
_LABEL_HEADER = re.compile(
    r"^(?![ \t])(?![ \t]*[-•*+][ \t])(?![ \t]*\d+[.)][ \t])(?!#)(?!\w{2,12}://)"
    r"(?:\*\*)?([^\n：:*`，。/]{41,120}?)(?:\*\*)?[ \t]*[：:](?!//)[ \t]*$", re.M)
_HEADING = re.compile(r"^(#{2,4})\s+(.+?)\s*$", re.M)
#: 正文第一行的一级标题（`# feat(scope): …`）是这篇的题目，不是一节的名字：留着它，
#: 改动的第一段会带上这行；当成标题，正文中间那种手误写成一级的提示行又会把一节切短
#: （#2670）。所以在正文最开头把它去掉。
_TITLE_LINE = re.compile(r"\A#[ \t]+[^\n]*\n+")
#: A line that is nothing but the name of a section — `Verification`, `验证`, `Tests` —
#: with no colon after it. The block below it is that section: a body puts the name on
#: its own line when a list follows, rather than beside the first sentence. Three things
#: keep a line of prose out of it: the name has to be one of the section's (§
#: `_names_a_section`), the line has to stand as a block of its own with nothing above it
#: but a blank line (§ `_stands_alone`), and it must not be a table row or carry a code
#: span — a sentence that wraps onto `repo checkout.`, a table's `| Check | Result |` and
#: a command in a code block all read as names otherwise. A list item is the section's
#: body and not another name for one: the first item under `Verification` so often
#: mentions a test that reading it as a name cut that whole section away.
_BARE_LABEL = re.compile(r"^(?![ \t]*[-•*+][ \t])(?![ \t]*\d+[.)][ \t])(?!#)"
                         r"(?:\*\*)?([^\n：:*|`]{1,20}?)(?:\*\*)?[ \t]*$", re.M)
#: A line that opens with a bold name and carries on after it: `**Verification** (branch
#: merged with `main` at 46f44333).`, `**Tests.** each new test fails on main`,
#: `**Checked**, in a throwaway container`. The name is not the whole line here and no
#: colon follows it, so neither `_LABEL` nor `_BARE_LABEL` reads one — and 17 of the 1000
#: merged bodies in the corpus name their verification half exactly this way, so the half
#: came back empty and the board said those bodies never wrote it.
#: What the bold span holds is the name; the half starts right after it, past a trailing
#: period or comma of the name's own (§ `_is_bold_label` for the two conditions the name
#: has to meet — a sentence that merely opens with a bold phrase is not a label line, the
#: same way #3618's is not one). Only the verification half is read this way: a body that
#: bolds `**Change**` or `**Fix**` says nothing new about where the changes end, while a
#: bold name taken for the wrong half truncates the one above it.
_BOLD_LABEL = re.compile(r"^\*\*([^\n*]{1,120}?)\*\*[ \t]*[.。，,]?[ \t]*", re.M)
#: The words that make a bold name the verification half's, and where they have to sit:
#: at the name's very start. A bold name has no colon and no whole line to lean on, so it
#: is the half's name outright (`verification`, `verified`, `tests`, `tested`, `checked`,
#: `valid`, `validation` — `verif` plus its endings is what makes `verification` and
#: `verified` one rule and not two). Where the word sits is the other half of the rule:
#: `_mentions` at the line's head would take `check` inside `checkpoints` (two bodies
#: write `**Old checkpoints.**` and `**Path checks.**`, sentences about something else),
#: and a bolded list of changes whose item happens to say `checked` halfway through its
#: sentence — `**The title that lands is the one the repository checked**` — would cut the
#: list at that item. Both are a name that does not start with the word.
_BOLD_NAME = re.compile(r"(?:verif|valid|test|check)(?:s|es|ed|ing|ied|ication|ation)?(?![a-z])"
                        r"|验证|怎么验|测试|自测", re.I)
_COMMENT_OPEN, _COMMENT_CLOSE = "<!--", "-->"
#: Everything from the first of these lines down is the platform's, not the author's.
_TRAILER_LINE = re.compile(r"^(?:Requested-by|Cheese-[A-Za-z-]+|Co-authored-by"
                           r"|Reviewed-by|Reported-by|Fixes-feedback):", re.M | re.I)
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
#: 标签行的名字要么点名这一节（`Verified on this machine:`、`Checks. Locally:`），要么短到
#: 只能是个名字（`验证：`、`Tests:`）。只看长短不够：一句话折行、那行又正好起一段时
#: （`Independent review at 2c3fb34a: three candidates raised…`）名字有 30 个字符，也在
#: `_LABEL` 的 40 以内 —— 它点名不了这一节，于是落到这里：30 > 20，挡住它的是这一条，
#: 下面那条 `_LABEL_LINE_MAX`（整行 80 > 60）排在它后面；而 `Verified on this machine:`
#: 有 25 个字符 —— 后者点名了这一节，这两条都管不着它。
_NAME_SHORT = 20
#: 名字长过 `_LABEL` 那条路（§ `_LABEL_HEADER`，41–120 个字符）时，还要看名字开头的几个词：
#: `Verification at final SHA 47c36ab3…`、`Behaviour, checked in a prototype …` 一上来就说了
#: 自己属于哪一半，而一句在行尾碰到冒号的话不会。四个词够到 `Each test below fails …` 里的
#: test，也拦得住 `The dev deploy of 452e49f (…) failed at …` 那一类。
_NAME_HEAD_WORDS = 4
#: 「这一行起不起一段」只看它上面两行就够，不用每次复制整个前缀：正文上限 64 KB，
#: 标签行多的时候那是平方的（四万个标签行 8 秒）。
_LOOK_BEHIND = 200
#: 短名字那条路还要看整行有多长：`验证：全过` 是一行，`Not covered by a test: the wiring\nfrom …` 是一句
#: 话了半行的话。点名这一节的那种标签不受这条限制（`Checks. Locally: \`pnpm install\`、eslint……`）。
_LABEL_LINE_MAX = 60


def _strip_comments(text):
    """The text with every `<!-- … -->` taken out.

    Scanning is linear and not a regex: a body is allowed to be 64 KB, and a `.` that
    crosses newlines hunting for a close it never finds is quadratic in the body's
    length. An unclosed `<!--` is left where it is — the regex kept it too."""
    out, i = [], 0
    while True:
        j = text.find(_COMMENT_OPEN, i)
        if j < 0:
            out.append(text[i:])
            return "".join(out)
        k = text.find(_COMMENT_CLOSE, j + len(_COMMENT_OPEN))
        if k < 0:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:j])
        i = k + len(_COMMENT_CLOSE)


def _section_rank(low, want):
    """How directly `low` says it is `want`'s section: 0 for the name's own word for it,
    1 for a word that names it less directly, None when it does not name it at all."""
    for rank, group in enumerate(_SECTION_WORDS[want]):
        if _mentions(low, group):
            return rank
    return None


def _mentions(low, words):
    """Whether `low` names a section with one of `words`.

    A word in the Latin alphabet has to start somewhere, not merely sit inside a
    longer one: `## Prefix handling` is not the change list, and the `change` inside
    `exchange` is not the word. A word written in another script is matched as it
    stands, because it has no letters to bound it.
    """
    for w in words:
        if not w.isascii():
            if w in low:
                return True
        elif re.search(r"\b%s" % re.escape(w), low):
            return True
    return False


def brief(desc, limit=400):
    """The description's 改了什么 / 怎么验证的, each cut to `limit` characters. An empty
    string means the description does not carry that section — the board says so
    instead of filling it in.

    Each half is read off the heading that names it; where no heading does, the
    opening paragraphs answer for "what changed" and a plain label line answers for
    the other half, which is how most bodies here are written."""
    text = _strip_comments(desc or "")
    trailer = _TRAILER_LINE.search(text)
    if trailer:
        text = text[:trailer.start()]
    agent = _AGENT_MARK.search(text)
    if agent:
        text = text[agent.end():]
    text = text.strip()
    title = _TITLE_LINE.match(text)
    if title:
        text = text[title.end():].strip()
    if not text:
        return "", ""
    heads = list(_HEADING.finditer(text))
    # 可能会切走一节的行，从早到晚：带冒号的标签行，和整行只有章节名的那些。
    labels = sorted([m for m in _LABEL.finditer(text) if _is_label(text, m)]
                    + [m for m in _LABEL_HEADER.finditer(text) if _is_label_header(text, m)]
                    + [m for m in _BARE_LABEL.finditer(text)
                       if _names_a_section(m.group(1)) and _stands_alone(text, m)]
                    + [m for m in _BOLD_LABEL.finditer(text) if _is_bold_label(text, m)],
                    key=lambda m: m.start())
    ver = [m for m in labels if _mentions(m.group(1).lower(), _VERIFIED_LABEL_WORDS)]
    verified = _under_heading(text, heads, "verified") or _under_label(text, ver, labels, heads, "verified")
    changed = _under_heading(text, heads, "changed", ver)
    if not changed:
        # 没有标题说「改了什么」时，正文开头到「验证」之前的那一段就是它 —— 前面是
        # 背景，后面是验证，中间剩下的全是改动。正文一上来就是标题（`## Summary`、
        # `## Fix` 这类别的叫法）时，改动写在那行标题底下：从标题之后起算，否则这里
        # 会切出一个空段，看板对着一份写了改动的正文说「没写这一节」。正文一上来就是
        # 验证那一节（开头那行标题是它，或第一个标签行就在第一行）时没有那一段：它
        # 底下、它后面是验证，改动没写在这里。
        lead = heads[0] if heads and heads[0].start() == 0 else None
        if (lead and _mentions(lead.group(2).lower(), _SECTION_WORDS_FLAT["verified"])) \
                or (ver and not text[:ver[0].start()].strip()):
            changed = ""
        else:
            begin = lead.end() if lead else 0
            stops = sorted(set([h.start() for h in heads] + [m.start() for m in ver]))
            ends = [s for s in stops if s >= begin]
            changed = text[begin:min(ends) if ends else len(text)].strip()
    return cut(changed, limit), cut(verified, limit)


def _under_heading(text, heads, want, also=()):
    """The body of the heading that names `want`, or "" when there is none.

    A heading that only mentions the word does not name the section: a change list whose
    item reads `… 各带自己的挂载测试` is not the verification section, and when a body
    also has a plain `## 验证方式` under it, that is the one meant. The shorter name is
    the one about the section, so it wins.

    A name that says the opposite loses to one that does not, however short: `## 没验证的`
    beside `## 怎么验证的` is the other half of what the reader has to know. It is still
    used when it is the only one there.

    `also` is where the section under this heading stops early: the verification half's
    label lines, so a `## 改动` does not read the `验证：` line that follows it as one
    more thing that changed."""
    hits = [(i, r) for i, h in enumerate(heads)
            if (r := _section_rank(h.group(2).lower(), want)) is not None]
    if not hits:
        return ""
    i = min(hits, key=lambda t: (_negates(heads[t[0]]), t[1],
                                 len(heads[t[0]].group(2)), heads[t[0]].start()))[0]
    begin = heads[i].end()
    # 紧贴标题的一行是本节自己的第一行，不是「本节到此为止」—— 只在它不点名另一半时
    # 才算本节内容：`## 改动` 底下的 `验证：ok` 一上来就点名（§ `_leads_with_a_half`），
    # 它切；`## 改动` 底下的 `只改测试：…` 是本节自己的正文，`测试` 在名字中段（#2735）。
    stops = [h.start() for h in heads[i + 1:]] \
        + [m.start() for m in also
           if m.start() > begin
           and (text[begin:m.start()].strip() or _leads_with_a_half(m.group(1)))]
    end = min(stops) if stops else len(text)
    return text[begin:end].strip()


def _is_label(text, m):
    """Whether a line that matched the label pattern really names a section.

    Two ways it does not, and both cut a section in half: the line is a sentence that
    wrapped so that a colon landed within the name's length (#2772), or it starts a block
    of its own the same way a label does but says nothing about either half — `Independent
    review at 2c3fb34a: three candidates raised…` (#3618), `Not covered by a test: the
    wiring from …` (#2733). Either the name names a section (`Verified on this machine:`,
    `Checks. Locally:`), or it is short enough to be nothing but a name and its line is
    too (`验证：全过`)."""
    if not _starts_a_block(text, m):
        return False
    if _names_a_section(m.group(1)):
        return True
    if len(_label_name(m)) > _NAME_SHORT:
        return False
    return len(_line_at(text, m)) <= _LABEL_LINE_MAX


def _line_at(text, m):
    """The whole line `m` matched, value included."""
    start = text.rfind("\n", 0, m.start()) + 1
    end = text.find("\n", m.end())
    return text[start:end if end >= 0 else len(text)]


def _label_name(m):
    """A label line's name, without the one bracketed aside it may carry after it."""
    return _label_name_ish(m.group(1))


def _names_a_section(name):
    """Whether a short unpunctuated line is one of the section names rather than prose."""
    return _mentions(name.lower(), _SECTION_NAME_WORDS)


def _label_name_ish(name):
    """A label line's name without its bracketed aside, from the name text alone
    (§ `_label_name`, which takes the match)."""
    for op in "（(":
        i = name.find(op)
        if i > 0:
            name = name[:i]
    return name.strip()


def _name_head(name):
    """A name's first few words, lowercased: what a long name leads with (§ `_NAME_HEAD_WORDS`)."""
    return " ".join(name.split()[:_NAME_HEAD_WORDS]).lower()


def _leads_with_a_half(name):
    """Whether `name` names one of the halves near its start.

    A label line tight against a heading is still a label when it says which half it is
    first: `验证：ok`, `Verification: ran tests` and `Local checks: …` are the other half
    starting, not the heading's own first line. `只改测试：…` is not — `测试` sits inside a
    longer name — and is that heading's own first line (#2735). Written in another script
    the word has to be the name's own first characters, because it has no letters to bound
    it; a Latin word is read within the name's first few (§ `_NAME_HEAD_WORDS`)."""
    low = _label_name_ish(name).lower()
    head = _name_head(low)
    for w in _SECTION_NAME_WORDS:
        if not w.isascii():
            if low.startswith(w):
                return True
        elif re.search(r"\b%s" % re.escape(w), head):
            return True
    return False


def _opens_with_a_section_word(name):
    """Whether a long name says which half it is near its start.

    `Verification at final SHA 47c36ab3…` and `Behaviour, checked in a prototype …` do.
    A sentence that ends a line on a colon and only mentions one of the words later does
    not — `The dev deploy of 452e49f (run 38037133758) failed at "Release the verified
    metering image":` — and reading one as a label cut the section above it in half."""
    return _mentions(_name_head(name), _SECTION_NAME_WORDS)


def _is_label_header(text, m):
    """Whether a whole line of nothing but a long name and a colon is a label line.

    The name is longer than `_LABEL` reads, so it gets the second condition (§
    `_LABEL_HEADER`): the line starts a block and leads with one half's name."""
    return _starts_a_block(text, m) and _opens_with_a_section_word(m.group(1))


def _is_bold_label(text, m):
    """Whether a line opening with a bold name and carrying on is a label line.

    Two conditions, the same shape as `_is_label_header`'s: the line starts a block, and
    the name opens with the verification half's own word. Without the first one, a
    sentence that merely wrapped with its opening in bold (#3618's shape, written in bold
    instead of plain) would cut the changes above it. Without the second one a body that
    merely bolds where it is going — `**Old checkpoints.**`, `**Path checks.**` — or bolds
    a change whose sentence happens to say `checked` would do the same. The half worth
    reading this way is the verification one; a `**Change**` says nothing a heading has
    not already said."""
    return _starts_a_block(text, m) and bool(_BOLD_NAME.match(m.group(1).lstrip()))


def _negates(h):
    """Whether a heading names the section by saying it is not that: `没验证的`, `No tests`."""
    low = h.group(2).strip().lower()
    if low.startswith(("没", "未", "无", "不")) or any(w in low for w in _NEGATING_PHRASES):
        return True
    return any(w in _NEGATING_WORDS for w in re.split(r"[\s:：,，.,()（）-]+", low)[:3])


def _starts_a_block(text, m):
    """Whether the line `m` matched begins a block of its own.

    A label line names a section, and a section starts at the top of the body, after a
    blank line, or under a heading. A sentence that merely wrapped onto a line whose
    text happens to reach a colon within the name's length does not: reading one as a
    label cut the section above it in half (PR #2772)."""
    before = text[max(0, m.start() - _LOOK_BEHIND):m.start()]
    if not before.strip():
        return True
    lines = before.rstrip(" \t").split("\n")
    above = lines[-2] if len(lines) >= 2 else ""
    if not lines[-1] and not above.strip():
        return True
    return not lines[-1] and above.lstrip().startswith("#")


def _stands_alone(text, m):
    """Whether the line `m` matched is a block of its own.

    A name on its own line has a blank line above it. A sentence that merely wrapped
    onto a short line does not, and taking one for a name cuts the section above it and
    hands the rest to the other half."""
    before = text[max(0, m.start() - _LOOK_BEHIND):m.start()].rstrip(" \t")
    return not before.strip() or before.endswith("\n\n")


def _under_label(text, hits, labels, heads, want):
    """The first hit's body, stopping at the next heading and at the next labelled line
    that names the other half: a heading under a label starts what comes after that
    section, and a body that runs past it hands the reader the next section's text as
    this one's. A label that names this same half — `Not covered by a test: …` — is one
    more sentence of it, not the end of it (#2733)."""
    if not hits:
        return ""
    after = [m.start() for m in heads if m.start() > hits[0].start()]
    after += [m.start() for m in labels
              if m.start() > hits[0].start() and _section_rank(m.group(1).lower(), want) is None]
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


def _job_log(job):
    """One Actions job's log. "" on a read that failed, which the caller treats as a job
    with no case names in it rather than as a reason to stop the sweep."""
    r = subprocess.run(["gh", "api", f"repos/{REPO[0]}/{REPO[1]}/actions/jobs/{job}/logs"],
                       capture_output=True, text=True)
    return r.stdout or ""


def failing_cases(checks, jobs=2, log_of=None):
    """The case names the failed jobs print, read out of their logs and quoted as they
    are — up to `_CASES_MAX` of them, and nothing about why they failed. A job whose
    log cannot be read contributes nothing rather than stopping the sweep.

    `log_of` is where a job's log comes from; the default reads it from GitHub. The
    self-test hands it its own text, so what is read out of a log — and how much of it
    there can be — is exercised without `gh` and without a network."""
    read = log_of or _job_log
    out = []
    for check in [c for c in checks if c.get("url")][:jobs]:
        job = _JOB_IN_URL.search(check["url"])
        if not job:  # 页面地址里没有 job 号：没有日志可读，不是一次读失败
            continue
        for raw in (read(job.group(1)) or "").splitlines()[-6000:]:
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


def row(kind, name, text, t, card, pr, waited, waiting, ready_at, last_ready, red_at, last_msg,
        pulse=None):
    good = pr is not None and "error" not in pr
    changed, verified = brief(pr.get("desc")) if good else ("", "")
    pulse = pulse or {}
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
        "last_event_at": pulse.get("last_event_at"),
        "last_message_at": pulse.get("last_message_at"),
        "last_message": pulse.get("last_message"),
        "last_output_at": pulse.get("last_output_at"),
        "running": pulse.get("running"),
        "heartbeat_min": pulse.get("heartbeat_min"),
        "read": good,
    }


def message_brief(m):
    """One line of a message, for the row: its author and the first _MESSAGE_BRIEF
    characters, whitespace collapsed so it stays on one line of the text list."""
    body = " ".join(str(m.get("content") or "").split())
    if len(body) > _MESSAGE_BRIEF:
        body = body[:_MESSAGE_BRIEF] + "…"
    return f"@{m.get('author') or '-'}「{body}」"


def heartbeat_of(last_msg, last_event, last_output=None):
    """The line's last sign of life: the newest of its last message, the last event
    block its seat wrote, and the running turn's last output when the platform gives it.
    A line that has no event blocks at all beats by its messages."""
    return max((x for x in (last_msg, last_event, last_output) if x), key=ts)


def in_flight(event, failed_after):
    """Whether the seat's latest event block is a tool call that has not returned: the
    platform writes the block when the call starts (that is its created_at) and adds
    `output_bytes` when it ends, so a tool block without one is a command still running
    — unless the turn failed after it, which leaves the block unmarked forever."""
    m = meta(event)
    return bool(m.get("tool")) and "output_bytes" not in m and not m.get("failed") and not failed_after


def stalled_for(now, heartbeat, silent, running=False):
    """Minutes since the heartbeat when that is at least `silent`, else None. A command
    still running is a heartbeat now, whatever the timestamps say."""
    if running:
        return None
    w = (now - ts(heartbeat)).total_seconds() / 60
    return w if w >= silent else None


def hhmm(iso):
    return f"{iso[11:16]} UTC" if iso else "没有"


def pulse_detail(waited, last_event, last_msg_at, last_msg_brief, last_output=None):
    """The 长时间无动静 line: how long without a heartbeat, then the facts the patrol
    needs before it nudges — when the seat last ran something, when the turn last
    produced output (only when the platform says), and the line's own last word."""
    return (f"{waited:.0f} 分钟无心跳：最后 event {hhmm(last_event)}，"
            + (f"最后输出 {hhmm(last_output)}，" if last_output else "")
            + f"最后消息 {hhmm(last_msg_at)} {last_msg_brief}")


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
        # 心跳：座位跑命令是 event 块，不是消息。只看消息会把一条正在跑长命令的线报成
        # 停摆（2026-10-13 #1468 跑了 70 分钟命令、被按「70 分钟没消息」催过）。event 只认
        # 座位自己写的：system 的 turn_failed 也是 event，但那不是它在动。
        sysev = get(tok, f"/topics/{tid}/history?limit=30&author=system")["data"]
        accev = get(tok, f"/topics/{tid}/history?limit=30&author=accept")["data"]
        sysev.sort(key=lambda m: m["created_at"])
        accev.sort(key=lambda m: m["created_at"])
        # 一条长命令只在开始时落一个 event 块、结束时才补 output_bytes，所以还没返回的
        # 命令是「现在在跑」；跑着的一轮每有输出平台还会更新任务行上的 last_output_at
        # （新版平台才有，没有就不算），单条 45 分钟的命令靠这两样才不会被报成停摆。
        seat = t.get("agent_handle")
        evs = get(tok, f"/topics/{tid}/history?limit=1&kind=event"
                       + (f"&author={seat}" if seat else ""))["data"]
        last_event = evs[-1]["created_at"] if evs else None
        failed_after = any(meta(m).get("event_type") == "turn_failed" and m["created_at"] > last_event
                           for m in sysev) if evs else False
        running = bool(evs) and in_flight(evs[-1], failed_after)
        heartbeat = heartbeat_of(last_msg, last_event, t.get("last_output_at"))
        pulse = {"last_event_at": last_event, "last_message_at": msg[-1]["created_at"] if msg else None,
                 "last_message": message_brief(msg[-1]) if msg else None,
                 "last_output_at": t.get("last_output_at"), "running": running,
                 "heartbeat_min": 0 if running else round(age(heartbeat))}

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

        # 红在哪只进看板：文本清单照老样子一卡一查，看板的模式才去读每个检查的判定，
        # 读日志拿用例名更贵，所以只在真的要摆出来的时候读。得在任何一行造出来之前
        # 读：同一张卡常常同时落进两个类别，先造的那一行会把还没算出来的用例记成空
        # 的，而类别排序后先被读到、也就是看板只印那一行的，正是它。
        red_recent = bool(red) and red[-1]["created_at"] > last_msg and age(red[-1]["created_at"]) >= 30
        if red_recent and want_facts and pr is not None and "error" not in pr and pr.get("cases") is None:
            pr["cases"] = failing_cases(pr.get("checks") or [])

        def add(kind, text, waited=None, waiting=None):
            rows.append(row(kind, name, text, t, card, pr, waited, waiting, ready_at, last_ready, red_at, last_msg, pulse))

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

        if red_recent:
            w = age(red[-1]["created_at"])
            where = ""
            if want_facts and pr is not None and "error" not in pr:
                names = [c["name"] for c in (pr.get("checks") or [])]
                where = "，红的是 " + "、".join(names) if names else ""
            add("红了没人修", f"{red[-1]['content'][:50]}{where}，{w:.0f} 分钟线上没新消息", w)

        if len(tail) >= 2 and all(meta(m)["event_type"] == "turn_failed" for m in tail[-2:]) and tail[-1]["created_at"] > last_msg:
            w = age(tail[-1]["created_at"])
            add("连续失败", f"最后 {tail[-1]['created_at'][11:16]} UTC：{said(tail[-1])}", w)

        w = stalled_for(now, heartbeat, silent, running)
        if w is not None:
            add("长时间无动静", pulse_detail(w, last_event, pulse["last_message_at"],
                                        pulse["last_message"] or "（还没有消息）",
                                        t.get("last_output_at")), w)

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
    那截、GitHub 没说红在哪个检查的，都如实说没有，看板不替它们补话；正文压根读不到
    的（还没有 PR，或 GitHub 上查不到）说的也是读不到，不写成作者没写。"""
    seen, out = set(), []
    for r in rows:
        if r["task"] in seen:
            continue
        seen.add(r["task"])
        out += [f"**#{r['task']} {r['title']}**｜{r['kind']}｜{r['stage']}", ""]
        if r.get("heartbeat_min") is not None:
            out += [f"心跳：{'命令在跑' if r.get('running') else str(r['heartbeat_min']) + ' 分钟前'}"
                    f"｜最后 event {hhmm(r['last_event_at'])}"
                    + (f"｜最后输出 {hhmm(r['last_output_at'])}" if r.get("last_output_at") else "")
                    + f"｜最后消息 {hhmm(r['last_message_at'])} {r['last_message'] or '（还没有消息）'}", ""]
        if not r["read"]:
            # 「没写这一节」是替作者下判断；读不到正文时说读不到，两件事分开。
            out += ["PR 正文：" + ("还没有 PR" if not r["pr"] else "GitHub 上看不到"), ""]
        else:
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


#: 离线自测的用例：每一份正文/每一行日志都是这个仓库里真实写过的形状，其中多数正是
#: 这一版修掉的那几种读法。改读段逻辑时先跑它。
_SELF_TEST_BODIES = (
    ("标题说 Change（单数）时读改动，不读它上面那段为什么",
     "## Why it broke\n\n背景：线上报了什么错。\n\n## Change\n\n- 把 A 换成 B\n",
     "- 把 A 换成 B", ""),
    ("有 Root cause 和 Fix 两个标题时读 Fix",
     "## Root cause\n\n坏在 A。\n\n## Fix\n\n把 A 换成 B。\n\n## Test\n\n跑过了\n",
     "把 A 换成 B。", "跑过了"),
    ("中文的 修改 和 改法 也是改动那一节",
     "## 现象\n\n坏的。\n\n## 修改\n\n改成 A。\n\n## 验证\n\n绿了\n",
     "改成 A。", "绿了"),
    ("缩进的列表项不是标签行，不切走上一节",
     "## 改动\n\nA。\n\n验证：\n  - 用例 a 过了\n  - 用例 b 过了\n",
     "A。", "- 用例 a 过了\n  - 用例 b 过了"),
    ("标签名字里的反引号和逗号不改变它是标签行",
     "改了 A。\n\n自测（`--self-test`，离线：不取 token、不连网）写在同一份文件里。\n\n验证：全过\n",
     "改了 A。\n\n自测（`--self-test`，离线：不取 token、不连网）写在同一份文件里。", "全过"),
    ("括号里点名的用例文件不挡住 Tests 这一行",
     "改了 A。\n\nTests (`test_a_b.py`): green\n",
     "改了 A。", "green"),
    ("Latest 里的 test 不是验证那一节",
     "## Latest conflict resolution\n\n合并了 main。\n\n验证：绿\n",
     "合并了 main。", "绿"),
    ("平台尾注之后的内容不算正文",
     "改动：A。\n\nCo-Authored-By: x <x@y>\n\nFixes-feedback: FB-1\n",
     "改动：A。", ""),
    ("标题只提了一句测试的清单不算验证节",
     "## 改动\n\n把 A 换成 B。\n\n## Checklist\n\n- [x] 各带自己的挂载测试\n\n## 验证方式\n\n绿了\n",
     "把 A 换成 B。", "绿了"),
    ("标题说「没改的部分」时，改动读到的是另一节",
     "## What did not change\n\nC 没动。\n\n## Fix\n\n把 A 换成 B。\n",
     "把 A 换成 B。", ""),
    ("标签名里带句点时它还是标签行",
     "改了 A。\n\nChecks. Locally: `pnpm install`, eslint 0 errors\n",
     "改了 A。", "`pnpm install`, eslint 0 errors"),
    ("折行出来的一行不算起一段，验证那一段不在这里断",
     "Own checks: read dream_prompt.py top to bottom in both renders; nothing\n"
     "else stopped me. Higher layer: CLAUDE.md says so.\n",
     "", "read dream_prompt.py top to bottom in both renders; nothing\n"
     "else stopped me. Higher layer: CLAUDE.md says so."),
    ("折行出来的一行名字够短，也仍然不算标签行",
     "改动：A。\n\n验证：读了两遍，没有别的问题\nHigher layer: the pool\n",
     "改动：A。", "读了两遍，没有别的问题\nHigher layer: the pool"),
    ("句子中间的一行只有章节名的样子，也不算标签行",
     "改动：把 A 换成 B。\n\nthe same\ntests\npass in both.\n",
     "改动：把 A 换成 B。\n\nthe same\ntests\npass in both.", ""),
    ("标签行底下遇到标题就到此为止",
     "改动：A。\n\n验证：绿了\n\n## 不能一起发的\n\n别的事。\n",
     "改动：A。", "绿了"),
    ("标签行贴在标题下面也算标签行",
     "## 背景\n验证：ok\n",
     "", "ok"),
    ("标签行前面的标题底下已经写了内容时，它才把这一节切在这儿",
     "## 问题\n\n线上的报错来自 A。\n\n## 改动\n\n只改测试：用一个 `ContextVar` 包住这次调用。\n\n## 验证\n\n绿了\n",
     "只改测试：用一个 `ContextVar` 包住这次调用。", "绿了"),
    ("名字长过四十个字符的标签行也算标签行",
     "改动：A。\n\nVerification at final SHA 47c36ab320e84053186d5d9e6acdfcfab7d0ee87:\n"
     "\n- 跑过 e2e。\n",
     "改动：A。", "- 跑过 e2e。"),
    ("长名字的一句话在行尾碰到冒号时不算标签行",
     "改动：A。\n\nThe dev deploy of 452e49f (run 38037133758) failed at \"Release the"
     " verified metering image\":\n\n- 重跑就过了。\n",
     "改动：A。\n\nThe dev deploy of 452e49f (run 38037133758) failed at \"Release the"
     " verified metering image\":\n\n- 重跑就过了。", ""),
    ("同一节的标签行不切断这一节",
     "验证：全过。\n\nNot covered by a test: the wiring needs a live box\n",
     "", "全过。\n\nNot covered by a test: the wiring needs a live box"),
    ("开头的一级标题是题目，不是改动",
     "# feat(x): 一句话标题\n\n正文第一段。\n\n## 验证\n\n绿了\n",
     "正文第一段。", "绿了"),
    ("短名字的标签行整行是一句话时不算标签行",
     "改了 A。\n\n本机跑过：`pytest -q` 全过，`ruff check` 0 error，改动那个文件上 `pyright` 也是 0 error\n",
     "改了 A。\n\n本机跑过：`pytest -q` 全过，`ruff check` 0 error，改动那个文件上 `pyright` 也是 0 error", ""),
    ("短名字的标签行只有名字和一小截值时还算标签行",
     "改了 A。\n\n本机自测：全过\n",
     "改了 A。", "全过"),
    ("平台写的注释不算正文",
     "<!-- board:start -->\n\n改动：A。\n\n验证：绿\n",
     "改动：A。", "绿"),
    ("整行只有章节名时，它底下的块是那一节",
     "改动如下。\n\nVerification\n\n- pytest 全过\n",
     "改动如下。", "- pytest 全过"),
    ("标题是 What 但名字在讲哪里错了时，改动读到 Fix 那一节",
     "## What was wrong\n\n线上的报错来自 A。\n\n## Fix\n\n把 A 换成 B。\n",
     "把 A 换成 B。", ""),
    ("Why / What / Checks 三个标题里，What 底下是改动",
     "## Why\n\n因为它慢。\n\n## What\n\n- 把 A 换成 B\n\n## Checks\n\n跑过了\n",
     "- 把 A 换成 B", "跑过了"),
    ("中文的 做了什么 也是改动那一节",
     "## 背景\n\n以前会慢。\n\n## 做了什么\n\n- 把 A 换成 B\n\n## 验证\n\n绿了\n",
     "- 把 A 换成 B", "绿了"),
    ("验证标签紧贴改动标题时，改动不读这条标签",
     "## 改动\n验证：ok\n",
     "", "ok"),
    ("验证节里一句半行的闲话不切走这一节（#3616）",
     "改动：把 A 换成 B。\n\n验证：绿了。\n\nReview at 2c3fb34a: two findings kept, "
     "neither of them blocking a merge.\n",
     "改动：把 A 换成 B。",
     "绿了。\n\nReview at 2c3fb34a: two findings kept, neither of them blocking a merge."),
    ("名字长过二十、又不点名某一半的一句话不算标签行（#3618）",
     "改动：把 A 换成 B。\n\n验证：绿了。\n\nFollow-up review at 2c3fb34a: two kept.\n",
     "改动：把 A 换成 B。", "绿了。\n\nFollow-up review at 2c3fb34a: two kept."),
    ("AGENT 标记之前的内容不算正文",
     "先记一句给自己看的话。\n\nAGENT:\n\n改动：A。\n\n验证：绿了\n",
     "改动：A。", "绿了"),
    ("一个标题同时点名两半时，验证另有它自己那一节",
     "## 改动和验证\n\n先改 A。\n\n## 验证\n\n绿了\n",
     "先改 A。", "绿了"),
    ("一个标题同时点名两半、又只有它一个时，两半读到同一段",
     "## 改动和验证\n\n跑了测试\n",
     "跑了测试", "跑了测试"),
    #: 这一组是那 17 份正文里被读漏的那种写法：正文自己把验证那一节写成「加粗名字 +
    #: 后话」，名字不是整行、后面也没有冒号，`_LABEL`、`_BARE_LABEL` 都读不到它。每条前面
    #: 只留一句改动，名字那行照抄原文 —— 这一条读的就是那一行怎么认出来（#3087、#3122
    #: 两条连后面那块一起抄，钉住「值从名字之后起算」）。第 17 份 #2647 不在这里：它两段
    #: 加粗讲的都是改动（前端单测怎么跑、e2e 怎么分片），正文里确实没有验证那一节。

    ("加粗名字那一行：后话是一句话（#3375）",
     "改动：A。\n"
     "\n"
     "**Checked**, in a throwaway container from the current gateway image with this branch's files mounted: `check_config.py` against this config, `test_system_billing_line.py`, `test_chatgpt_session.py`, `test_deepseek_route_order.py` all pass. The new test fails when the hook leaves `system` alone.",
     "改动：A。", "in a throwaway container from the current gateway image with this branch's files mounted: `check_config.py` against this config, `test_system_billing_line.py`, `test_chatgpt_session.py`, `test_deepseek_route_order.py` all pass. The new test fails when the hook leaves `system` alone."),
    ("加粗名字那一行：名字带句点，后话用例名起头（#3319）",
     "改动：A。\n"
     "\n"
     "**Test.** `test_renaming_a_task_hands_back_the_task_as_its_page_reads_it`: the rename's answer carries the same `presentation` and `waiting_on` as reading the task. It fails on main (`presentation` is `None`) and passes here",
     "改动：A。", "`test_renaming_a_task_hands_back_the_task_as_its_page_reads_it`: the rename's answer carries the same `presentation` and `waiting_on` as reading the task. It fails on main (`presentation` is `None`) and passes here"),
    ("加粗名字那一行：后话是括号，再跟一列分节（#3122）",
     "改动：A。\n"
     "\n"
     "**Verification** (branch merged with `main` at 46f44333).\n"
     "\n"
     "\n"
     "\n"
     "- Backend: `tests/integration/test_panel_live_refresh.py` and `test_task_naming.py` 39 passed, including the new `test_renaming_a_channel_names_that_channel` and the room-id assertion on the frame the task's own conversation hears; the accept-card, task-page, archive and bulk-switch suites 128 passed. `scripts/check_boundaries.py` 3 contracts kept, 0 broken, no stale entries; `scripts/check_deferred_imports.py` 797 unannotated imports in 175 files, the baseline, unchanged; ruff and ruff-format clean; `check-file-sizes.py` passes for all changed files.\n"
     "- Frontend: `ChatPanel.siteFrames`, `TopicView.feedbackLive`, `workspace.requests` and `TopicChatColumn` 42 passed; eslint, prettier, the hardcoded-Chinese gate and the boundary, design-token, scene and catalog ratchets are clean. `vue-tsc` passed on the tree before the merge with `main`; on the merged tree this box killed the local run (out of memory), so CI is the authority on it here.\n"
     "- The same suites run in CI on this PR.",
     "改动：A。", "(branch merged with `main` at 46f44333).\n\n- Backend: `tests/integration/test_panel_live_refresh.py` and `test_task_naming.py` 39 passed, including the new `test_renaming_a_channel_names_that_channel` and the room-id assertion on the frame the task's own conversation hears; the accept-card, task-page, archive and bulk-switch suites 128 passed. `scripts/check_boundaries.py` 3 contracts kept, 0 broken, no stale entries; `scripts/check_deferred_imports.py` 797 unannotated imports in 175 files, the baseline, unchanged; ruff and ruff-format clean; `check-file-sizes.py` passes for all changed files.\n- Frontend: `ChatPanel.siteFrames`, `TopicView.feedbackLive`, `workspace.requests` and `TopicChatColumn` 42 passed; eslint, prettier, the hardcoded-Chinese gate and the boundary, design-token, scene and catalog ratchets are clean. `vue-tsc` passed on the tree before the merge with `main`; on the merged tree this box killed the local run (out of memory), so CI is the authority on it here.\n- The same suites run in CI on this PR."),
    ("加粗名字那一行：名字带定语，后话是括号（#3116）",
     "改动：A。\n"
     "\n"
     "**Verified on the owner's Mac** (macOS, arm64), under `sandbox-exec -p '(version 1)(allow default)'` with the connector's Python 3.13:",
     "改动：A。", "(macOS, arm64), under `sandbox-exec -p '(version 1)(allow default)'` with the connector's Python 3.13:"),
    ("加粗名字那一行：名字带句点，后话用例名起头（#3110）",
     "改动：A。\n"
     "\n"
     "**Test.** `test_an_own_session_on_a_mac_finds_its_owners_login_keychain` runs the real launcher on a faked Mac (`uname` says Darwin, and a fake `security` records its `HOME`). It checks that the keychain lookup gets the machine's home while the session keeps the room's. Without the fix it fails with the room's home.",
     "改动：A。", "`test_an_own_session_on_a_mac_finds_its_owners_login_keychain` runs the real launcher on a faked Mac (`uname` says Darwin, and a fake `security` records its `HOME`). It checks that the keychain lookup gets the machine's home while the session keeps the room's. Without the fix it fails with the room's home."),
    ("加粗名字那一行：后话是括号加冒号（#3097）",
     "改动：A。\n"
     "\n"
     "**Test** (`deploy/tests/test-app-tier-health.sh`, `rollout-rollback-b`): a rollout that starts while `backend-b` serves and then fails its health check. It must log `previous=oldsha`, start the release in `backend`, and roll back by starting the previous images in `backend-b`. On main's script the test fails with \"a release started while backend-b served found no previous release\". The existing `rollout-rollback` test still passes",
     "改动：A。", "(`deploy/tests/test-app-tier-health.sh`, `rollout-rollback-b`): a rollout that starts while `backend-b` serves and then fails its health check. It must log `previous=oldsha`, start the release in `backend`, and roll back by starting the previous images in `backend-b`. On main's script the test fails with \"a release started while backend-b served found no previous release\". The existing `rollout-rollback` test still passes"),
    ("加粗名字那一行：后话是括号，值另起一行（#3094）",
     "改动：A。\n"
     "\n"
     "**Tests** (`deploy/tests/test_auto_deploy.py`, `SupersededRelease`, driving `main()` against a fake GitHub API):",
     "改动：A。", "(`deploy/tests/test_auto_deploy.py`, `SupersededRelease`, driving `main()` against a fake GitHub API):"),
    ("加粗名字那一行：后话是括号，值另起一行（#3093）",
     "改动：A。\n"
     "\n"
     "**Tests** (`deploy/tests/test_auto_deploy.py`, against a fake `urlopen`):",
     "改动：A。", "(`deploy/tests/test_auto_deploy.py`, against a fake `urlopen`):"),
    ("加粗名字那一行：名字带句点，后话用例文件名起头（#3091）",
     "改动：A。\n"
     "\n"
     "**Tests.** `test_a_thread_hears_of_a_task_made_from_its_message.py` follows the dev sequence with the real routes and turn. The teammate answers in a 支线, the person turns the message into a task, and the teammate is asked again. The test checks that the message sent to the resumed session carries the marked task. It fails on main and passes here.",
     "改动：A。", "`test_a_thread_hears_of_a_task_made_from_its_message.py` follows the dev sequence with the real routes and turn. The teammate answers in a 支线, the person turns the message into a task, and the teammate is asked again. The test checks that the message sent to the resumed session carries the marked task. It fails on main and passes here."),
    ("加粗名字那一行：名字带句点，后话用例文件名起头（#3089）",
     "改动：A。\n"
     "\n"
     "**Tests.** `test_an_agent_knows_its_own_name.py` renames the room's teammate to Nova, @-s it in a 支线, and checks that the system prompt the session was started with opens with its name. It fails on main and passes here. `tests/unit` passes except 18 failures and 2 errors in network- and port-bound tests (`test_fetch_guard`, `test_preview_instances`, `test_private_egress`, `test_preview_captured_port`, `test_session_host_bounds`, one device-workspace test). The same set fails on unmodified main on the same machine",
     "改动：A。", "`test_an_agent_knows_its_own_name.py` renames the room's teammate to Nova, @-s it in a 支线, and checks that the system prompt the session was started with opens with its name. It fails on main and passes here. `tests/unit` passes except 18 failures and 2 errors in network- and port-bound tests (`test_fetch_guard`, `test_preview_instances`, `test_private_egress`, `test_preview_captured_port`, `test_session_host_bounds`, one device-workspace test). The same set fails on unmodified main on the same machine"),
    ("加粗名字那一行：后话是一句总结，底下跟一列用例（#3087）",
     "改动：A。\n"
     "\n"
     "**Tests.** Each new test fails on main and passes here:\n"
     "\n"
     "- `test_agent_mentions.py::test_a_person_mentioned_by_a_teammate_is_told_the_teammates_own_name`\n"
     "- `test_cheese_question_notice.py::test_the_mail_about_a_question_names_the_teammate_that_asked`, which renames the teammate to Nova, asks, and renders the queued mail\n"
     "- `test_integrations.py::test_the_room_line_names_the_teammate_that_drafted`\n"
     "- `test_notification_letter.py::test_a_question_mail_calls_the_teammate_by_its_name`",
     "改动：A。", "Each new test fails on main and passes here:\n\n- `test_agent_mentions.py::test_a_person_mentioned_by_a_teammate_is_told_the_teammates_own_name`\n- `test_cheese_question_notice.py::test_the_mail_about_a_question_names_the_teammate_that_asked`, which renames the teammate to Nova, asks, and renders the queued mail\n- `test_integrations.py::test_the_room_line_names_the_teammate_that_drafted`\n- `test_notification_letter.py::test_a_question_mail_calls_the_teammate_by_its_name`"),
    ("加粗名字那一行：名字带句点，后话用例文件名起头（#3048）",
     "改动：A。\n"
     "\n"
     "**Tests.** `ChatPanel.summonWhereAnswered.spec.ts` has four cases: main line (fails on main), task conversation, private chat, archived channel. The chat, room and composer specs all pass locally (101 files)",
     "改动：A。", "`ChatPanel.summonWhereAnswered.spec.ts` has four cases: main line (fails on main), task conversation, private chat, archived channel. The chat, room and composer specs all pass locally (101 files)"),
    ("加粗名字那一行：名字带句点，后话一句总结（#3039）",
     "改动：A。\n"
     "\n"
     "**Tests.** The new `TaskRelatedList.spec.ts` checks that a mentioned agent reads as `@Nova` and that a handle the roster does not know reads as `@bob`. Both checks fail on main and pass here",
     "改动：A。", "The new `TaskRelatedList.spec.ts` checks that a mentioned agent reads as `@Nova` and that a handle the roster does not know reads as `@bob`. Both checks fail on main and pass here"),
    ("加粗名字那一行：名字带句点，后话 pytest 节点名（#3038）",
     "改动：A。\n"
     "\n"
     "**Tests.** `test_doc_edit_notices.py::test_an_ai_teammate_is_named_by_its_handle_like_a_person` stores edits from the room's agent and from a person, then checks that the line carries both handles and not 芝士. It fails on main and passes here",
     "改动：A。", "`test_doc_edit_notices.py::test_an_ai_teammate_is_named_by_its_handle_like_a_person` stores edits from the room's agent and from a person, then checks that the line carries both handles and not 芝士. It fails on main and passes here"),
    ("加粗名字那一行：名字带句点，后话一句总结（#3035）",
     "改动：A。\n"
     "\n"
     "**Tests.** `test_task_naming.py` has two new tests. In one, a task is turned from a message and named from it. In the other, the opener says too little and the task is named from what its 支线 said. Both fail on main and pass here",
     "改动：A。", "`test_task_naming.py` has two new tests. In one, a task is turned from a message and named from it. In the other, the opener says too little and the task is named from what its 支线 said. Both fail on main and pass here"),
    ("加粗名字那一行：名字带句点，后话一句总结（#3025）",
     "改动：A。\n"
     "\n"
     "**Tests.** The fake bucket in `test_sandbox_idle_stop.py` now speaks the S3 protocol, and the platform reaches it through the real `S3StorageBackend`. Three new tests: a sweep cancelled mid-archive, an archive that outlasts one sweep's wait, and a part the bucket drops. All three fail on main and pass here.",
     "改动：A。", "The fake bucket in `test_sandbox_idle_stop.py` now speaks the S3 protocol, and the platform reaches it through the real `S3StorageBackend`. Three new tests: a sweep cancelled mid-archive, an archive that outlasts one sweep's wait, and a part the bucket drops. All three fail on main and pass here."),
    # 验半段写在别的名字底下：`valid` 那一族（§ `_VERIFIED_WORDS`）、说反话的标题
    # （§ `_NEGATING_PHRASES`）、平台那几行之前的部分（§ `_TRAILER_LINE` 的 `Cheese-`）
    # —— 三条规则各自的用例，去掉哪条都有一处变红。
    ("验证段写成 `## Validation` 也读得出",
     "改动：A。\n"
     "\n"
     "## Validation\n"
     "\n"
     "跑了 X。",
     "改动：A。", "跑了 X。"),
    ("标题说「这里没有改变」的那一节不算改动段（_NEGATING_PHRASES）",
     "## 改动没有改变的东西\n"
     "\n"
     "甲段。\n"
     "\n"
     "## Fix\n"
     "\n"
     "乙段。",
     "乙段。", ""),
    ("平台那几行（`Cheese-Task:`）之前的部分才算正文",
     "改动：A。\n"
     "\n"
     "Cheese-Task: 12\n"
     "\n"
     "**Tests.** 全过。",
     "改动：A。", ""),
    # 下面四条各自盯住一条规则，原句上改一处就会红（见 `_self_test_patterns` 同一思路）：
    # 名字里带 `test` 的长名字（§ `_SECTION_NAME_WORDS` 的改动词）、结尾带冒号的列表项
    # （§ `_LABEL` 的列表项前瞻）、名字后面挂长括号（§ `_label_name_ish`）、正文一上来
    # 就是验证那一节（§ `brief` 的 `lead`）。
    ("名字里带 test 的长名字靠改动词才认出是哪一半（#3137 那句 `Each change has one regression test that fails without it. Frontend:`）",
     "Five readings in the observability reporting chain named the wrong thing, so\n"
     "each told the platform a story that disagreed with what happened:\n"
     "\n"
     "Each change has one regression test that fails without it. Frontend:\n"
     "\n"
     "跑了 X。",
     "Five readings in the observability reporting chain named the wrong thing, so\n"
     "each told the platform a story that disagreed with what happened:",
     "跑了 X。"),
    ("验证那一节底下是一个列表，列表项结尾的冒号不算标签行（#3395）",
     "改动甲。\n"
     "\n"
     "验证：\n"
     "\n"
     "- 用真浏览器历史复现了上面两条链子：改动前点下去成环，改动后一路点停在待办。\n"
     "- `vitest run …`：37 项通过。\n",
     "改动甲。",
     "- 用真浏览器历史复现了上面两条链子：改动前点下去成环，改动后一路点停在待办。\n"
     "- `vitest run …`：37 项通过。"),
    ("标签行的名字后面挂一长串括号时，括号不算进名字（_label_name_ish）",
     "改动甲。\n"
     "\n"
     "验证：\n"
     "\n"
     "跑了 X。\n"
     "\n"
     "Locally green (see the CI run):\n"
     "\n"
     "别的。\n",
     "改动甲。",
     "跑了 X。"),
    ("正文一上来就是验证那一节时没有改动段（brief 的 lead）",
     "## Verification\n"
     "\n"
     "跑了 X。",
     "", "跑了 X。"),
)
_SELF_TEST_ROLLUPS = (
    ("FAILURE 的 CheckRun 算红", {"contexts": {"nodes": [
        {"__typename": "CheckRun", "name": "guards / guards", "conclusion": "FAILURE",
         "detailsUrl": "https://github.com/x/y/actions/runs/1/job/2"}]}}, ["guards / guards"]),
    ("SUCCESS 的不算红", {"contexts": {"nodes": [
        {"__typename": "CheckRun", "name": "guards / guards", "conclusion": "SUCCESS",
         "detailsUrl": "https://github.com/x/y/actions/runs/1/job/2"}]}}, []),
    ("老式 StatusContext 的 FAILURE 也算红", {"contexts": {"nodes": [
        {"__typename": "StatusContext", "context": "ci/old", "state": "FAILURE",
         "targetUrl": "https://github.com/x/y/actions/runs/1/job/3"}]}}, ["ci/old"]),
    # 判定集合里剩下这三个：不常见，但没跑成的检查不止 FAILURE 一种写法，各有一条用例
    # 才谈得上它们是这条规则、而不是那句 `in ("FAILURE", "ERROR")` 的另一半。
    ("TIMED_OUT 的 CheckRun 算红", {"contexts": {"nodes": [
        {"__typename": "CheckRun", "name": "guards / guards", "conclusion": "TIMED_OUT",
         "detailsUrl": "https://github.com/x/y/actions/runs/1/job/4"}]}}, ["guards / guards"]),
    ("STARTUP_FAILURE 的 CheckRun 算红", {"contexts": {"nodes": [
        {"__typename": "CheckRun", "name": "guards / guards", "conclusion": "STARTUP_FAILURE",
         "detailsUrl": "https://github.com/x/y/actions/runs/1/job/5"}]}}, ["guards / guards"]),
    ("ACTION_REQUIRED 的 CheckRun 算红", {"contexts": {"nodes": [
        {"__typename": "CheckRun", "name": "guards / guards", "conclusion": "ACTION_REQUIRED",
         "detailsUrl": "https://github.com/x/y/actions/runs/1/job/6"}]}}, ["guards / guards"]),
    ("没跑完的判定（NEUTRAL）不算红", {"contexts": {"nodes": [
        {"__typename": "CheckRun", "name": "guards / guards", "conclusion": "NEUTRAL",
         "detailsUrl": "https://github.com/x/y/actions/runs/1/job/7"}]}}, []),
)
_SELF_TEST_LOG_LINES = (
    ("2026-10-13T04:11:00.1234567Z FAILED tests/unit/test_a.py::test_b", "tests/unit/test_a.py::test_b"),
    ("##[error]tests/unit/test_a.py::test_b", "tests/unit/test_a.py::test_b"),
    ("--- FAIL: TestTheThing", "TestTheThing"),
    ("✕ renders the settings page", "renders the settings page"),
    ("2026-10-13T04:11:00.1234567Z ok, nothing failed here", ""),
)

#: 看板那句指标从这一份上拼出来，字段就是 `day_metrics` 会返回的那些。
_SELF_TEST_METRICS = {
    "date": "2026-10-13",
    "merge_to_queue": {"samples": 2, "median_min": 4.0, "over_10_min_cards": 1,
                       "over_10_min": 1, "over_10_min_detail": [("72", 12)]},
    "failed_turns": {"total": 3, "by_line": {"17": 3}},
}


def _self_pr(desc, **over):
    """A PR as `gh_pr` returns one, carrying the fields `row` and `stage_of` read."""
    pr = {"desc": desc, "head": "abcdef1234567890", "ci": "SUCCESS",
          "review_head": "abcdef1234567890", "merged": False, "mergeQueueEntry": None,
          "timelineItems": {"nodes": []}, "checks": [], "cases": None}
    pr.update(over)
    return pr


def _self_row(desc="改动：换了缓存。\n\n**Tests.** 全过。", pr=True, card=True,
              kind="可采纳未采纳", waited=7, waiting="wangchangxin", number=1, title="甲"):
    """One row as `sweep` builds it, from a description and little else. `pr=False` is
    the card whose PR was not read, `card=False` the line that never had one."""
    t = {"number": number, "title": title, "id": f"t{number}", "agent_handle": "cheese"}
    c = {"pr_number": 42, "status": "pending"} if card else {}
    p = _self_pr(desc) if pr is True else (pr if isinstance(pr, dict) else None)
    return row(kind, f"#{number} {title} @cheese", "细节", t, c, p, waited, waiting,
               None, None, None, "2026-10-13T00:00:00Z")


def _self_test_board():
    """(name, got, want) for 组装两段那一步：`row` 把一次读到的 PR 装成一行，`details`
    和 `render` 再把这行摆出来。两段各自加粗一行、正文按 `> ` 引，正文没写的那一段说
    「没写这一节」，正文读不到的两种情形各说各的，红在哪个检查、哪些用例，表格里 CI
    用字／审查钉对不上标「（旧）」／没在等的写 `—`，同一个任务只说一次，以及一条
    信号都没有时的那一句。这几条各有一处变了就红。"""
    board = lambda rows: render(rows, _SELF_TEST_METRICS, 60,
                                datetime(2026, 10, 13, 5, 0), "12345678-ab")
    detail = lambda rows: "\n".join(details(rows)).strip()
    one = "**#1 甲**｜可采纳未采纳｜递卡后等 CI 审查"
    return [
        ("每个待判 PR：两段各自加粗一行，正文按 `> ` 引",
         detail([_self_row()]),
         one + "\n\n改了什么：\n> 改动：换了缓存。\n\n怎么验证的：\n> 全过。"),
        ("每个待判 PR：正文没写的那一段说「没写这一节」",
         detail([_self_row(desc="改动：A。")]),
         one + "\n\n改了什么：\n> 改动：A。\n\n怎么验证的：PR 正文里没写这一节"),
        ("每个待判 PR：还没有 PR 的那条说「还没有 PR」",
         detail([_self_row(pr=False, card=False)]),
         "**#1 甲**｜可采纳未采纳｜在做未递卡\n\nPR 正文：还没有 PR"),
        ("每个待判 PR：GitHub 上查不到的那条说「GitHub 上看不到」",
         detail([_self_row(pr={"error": "boom"})]),
         "**#1 甲**｜可采纳未采纳｜GitHub 查不到\n\nPR 正文：GitHub 上看不到"),
        ("每个待判 PR：红在哪个检查、哪些用例都摆出来",
         detail([_self_row(pr=_self_pr("改动：A。\n\n**Tests.** 全过。",
                                       checks=[{"name": "guards / guards", "url": "u"},
                                               {"name": "unit", "url": "u"}],
                                       cases=["tests/a.py::b", "tests/c.py::d"]))]),
         one + "\n\n改了什么：\n> 改动：A。\n\n怎么验证的：\n> 全过。\n"
         "\n红的是：guards / guards、unit\n失败用例：\n> tests/a.py::b\n> tests/c.py::d"),
        ("每个待判 PR：同一个任务在两类里只说一次",
         detail([_self_row(), _self_row(kind="长时间无动静")]).count("**#1 甲**"), 1),
        ("表格：CI 用字、审查钉对不上标「（旧）」、没在等的写「—」",
         [ln for ln in board([_self_row(),
                              _self_row(number=2, title="乙", waited=None, waiting=None,
                                        pr=_self_pr("改动：B。", head="abcdef1234567890",
                                                    review_head="0123456789abcdef",
                                                    ci="FAILURE"))]).split("\n")
          if ln.startswith("| ") and "| #" in ln],
         ["| 可采纳未采纳 | #1 | 递卡后等 CI 审查 | 绿 | abcdef12 ✓ | 7 分 | wangchangxin |",
          "| 可采纳未采纳 | #2 | 递卡后等 CI 审查 | 红 | 01234567（旧） | — | — |"]),
        ("有信号时：表头、频道那行、每个待判 PR 那一段都在",
         [ln for ln in board([_self_row()]).split("\n")
          if ln.startswith(("UTC ", "| 类别", "### "))],
         ["UTC 2026-10-13 05:00｜信号阈值 60 分钟｜1 条｜频道 12345678",
          "| 类别 | 任务 | 到哪一步 | CI | 审查钉 | 卡了 | 在等 |",
          "### 每个待判 PR"]),
        ("一条信号都没有时：不摆表格，只说一句",
         board([]),
         "## 调度看板\n\nUTC 2026-10-13 05:00｜信号阈值 60 分钟｜0 条｜频道 12345678\n"
         "\n各线无待处理信号。\n\n" + metrics_line(_SELF_TEST_METRICS)),
    ]


def _self_test_pulse():
    """(name, got, want) for 心跳那一步：最后一条消息和最后一个 event 块谁新谁算；
    摘要压成一行、截到上限；停摆那一行把两件事都摆出来，没有 event 的线说「没有」；
    看板每个任务下面多一行心跳，老的用例没给心跳就没有这一行。"""
    msg = {"author": "cheese-a", "content": "审查在跑，\n  等它出结论。"}
    now = datetime(2026, 10, 13, 2, 0, tzinfo=timezone.utc)
    call = {"meta": {"tool": "Bash", "arg": "跑测试"}}
    done = {"meta": {"tool": "Bash", "arg": "跑测试", "output_bytes": 12}}
    return [
        ("心跳取消息和 event 里新的那个",
         heartbeat_of("2026-10-13T01:00:00Z", "2026-10-13T01:40:00Z"), "2026-10-13T01:40:00Z"),
        ("没有 event 块的线按消息算心跳",
         heartbeat_of("2026-10-13T01:00:00Z", None), "2026-10-13T01:00:00Z"),
        ("平台给了 last_output_at（+00:00 写法）且更新，心跳取它",
         heartbeat_of("2026-10-13T01:00:00Z", "2026-10-13T01:40:00Z", "2026-10-13T01:55:00+00:00"),
         "2026-10-13T01:55:00+00:00"),
        ("停摆判定：心跳 60 分钟前、阈值 40，报 60",
         stalled_for(now, "2026-10-13T01:00:00Z", 40), 60.0),
        ("停摆判定：消息 60 分钟前但 event 20 分钟前，不报（回退成只看消息这里就红）",
         stalled_for(now, heartbeat_of("2026-10-13T01:00:00Z", "2026-10-13T01:40:00Z"), 40), None),
        ("停摆判定：命令还在跑，时间戳再旧也不报",
         stalled_for(now, "2026-10-13T00:00:00Z", 40, running=True), None),
        ("在跑：tool 块没有 output_bytes 就是还没返回", in_flight(call, failed_after=False), True),
        ("在跑：补了 output_bytes 的是返回了的", in_flight(done, failed_after=False), False),
        ("在跑：之后有 turn_failed 的块是被扔下的，不算在跑", in_flight(call, failed_after=True), False),
        ("在跑：progress 这类不是 tool 的 event 不算", in_flight({"meta": {"progress": 1}}, False), False),
        ("消息摘要：作者加正文一行，换行压掉",
         message_brief(msg), "@cheese-a「审查在跑， 等它出结论。」"),
        ("消息摘要：超过上限截断并标省略",
         message_brief({"author": "a", "content": "x" * 100}), "@a「" + "x" * 80 + "…」"),
        ("停摆那一行：无心跳分钟数、最后 event、最后消息都在",
         pulse_detail(52.4, "2026-10-13T01:40:00Z", "2026-10-13T01:00:00Z", "@a「审查在跑」"),
         "52 分钟无心跳：最后 event 01:40 UTC，最后消息 01:00 UTC @a「审查在跑」"),
        ("停摆那一行：没有 event 的线说「没有」",
         pulse_detail(45, None, "2026-10-13T01:00:00Z", "@a「等」"),
         "45 分钟无心跳：最后 event 没有，最后消息 01:00 UTC @a「等」"),
        ("停摆那一行：平台给了最后输出就一并写",
         pulse_detail(45, "2026-10-13T01:00:00Z", "2026-10-13T01:00:00Z", "@a「等」", "2026-10-13T01:10:00+00:00"),
         "45 分钟无心跳：最后 event 01:00 UTC，最后输出 01:10 UTC，最后消息 01:00 UTC @a「等」"),
        ("看板：带心跳的行在标题下多一行心跳",
         [ln for ln in details([row("长时间无动静", "#1 甲 @cheese", "细节",
                                    {"number": 1, "title": "甲", "id": "t1", "agent_handle": "cheese"},
                                    {}, None, 52, None, None, None, None, "2026-10-13T00:00:00Z",
                                    {"last_event_at": "2026-10-13T01:40:00Z",
                                     "last_message_at": "2026-10-13T01:00:00Z",
                                     "last_message": "@a「审查在跑」", "heartbeat_min": 52})])
          if ln.startswith("心跳：")],
         ["心跳：52 分钟前｜最后 event 01:40 UTC｜最后消息 01:00 UTC @a「审查在跑」"]),
        ("看板：命令在跑的行说「命令在跑」",
         [ln for ln in details([row("可采纳未采纳", "#1 甲 @cheese", "细节",
                                    {"number": 1, "title": "甲", "id": "t1", "agent_handle": "cheese"},
                                    {}, None, 5, None, None, None, None, "2026-10-13T00:00:00Z",
                                    {"last_event_at": "2026-10-13T01:40:00Z", "last_message_at": None,
                                     "last_message": None, "running": True, "heartbeat_min": 0})])
          if ln.startswith("心跳：")],
         ["心跳：命令在跑｜最后 event 01:40 UTC｜最后消息 没有 （还没有消息）"]),
        ("看板：没给心跳的行（老用例）不多这一行",
         [ln for ln in details([_self_row()]) if ln.startswith("心跳：")], []),
    ]


def _self_test_patterns():
    """(name, got, want) for 读段底下那几条规则各自的最小用例：整行名字那两个上界、
    日志时间戳连着的那个空格、注释的两种情形、抽取那一步的截断和压空行。1000 份正文
    这一层到不了它们，去掉了也看不出来。"""
    long_name = lambda n: "Verification " + "f" * (n - 13) + ":"
    return [
        ("整行名字 120 个字符，仍算标签行（_LABEL_HEADER 的上界）",
         bool(_LABEL_HEADER.search("改动：A。\n\n" + long_name(120) + "\n")), True),
        ("整行名字 121 个字符，不算标签行",
         bool(_LABEL_HEADER.search("改动：A。\n\n" + long_name(121) + "\n")), False),
        ("日志行的时间戳连它后面那个空格一起去掉（_LOG_STAMP 的 `\\s?`）",
         _LOG_STAMP.sub("", "2026-10-13T04:11:00.1234567Z FAILED a::b"), "FAILED a::b"),
        ("没有闭合的 `<!--` 原样留着（_strip_comments）",
         _strip_comments("前 <!-- 后"), "前 <!-- 后"),
        ("闭合的 `<!-- … -->` 连同里面的一起去掉",
         _strip_comments("前 <!-- 注释 --> 后"), "前  后"),
        ("超长的那一节截断时说一声（cut）",
         cut("x" * 30, 10), "x" * 10 + " …（截断）"),
        ("一节里的空行压成一行（cut）",
         cut("a\n\n\n\nb", 100), "a\n\nb"),
    ]


def _self_test_jobs():
    """(name, got, want) for 读失败用例那一步（§ `failing_cases`）：日志从 `log_of`
    来，所以这一组不开网络、不调 `gh`。顺序、去重、条数上限、页面上没有 job 号的那
    种检查、只读前两个（`jobs=2`）、以及一次读不到不影响别的检查。"""
    urls = lambda *jobs: [{"name": f"job {j}",
                           "url": f"https://github.com/x/y/actions/runs/1/job/{j}"}
                          for j in jobs]
    logs = {
        "11": "\n".join(["2026-10-13T04:11:00.1234567Z FAILED tests/a.py::x",
                         "FAILED tests/b.py::y",
                         "2026-10-13T04:11:01.0000000Z FAILED tests/a.py::x"]),
        "12": "\n".join(f"FAILED tests/j{i}.py::t" for i in range(8)),
        "13": "##[error]tests/z.py::w",
    }
    read = lambda j: logs.get(j, "")
    return [
        ("日志里的用例名按出现顺序读，重复的只留一次",
         failing_cases(urls("11"), log_of=read), ["tests/a.py::x", "tests/b.py::y"]),
        ("用例名最多留 6 条（_CASES_MAX）",
         failing_cases(urls("12"), log_of=read), [f"tests/j{i}.py::t" for i in range(6)]),
        ("检查页面上没有 job 号时不读它的日志（_JOB_IN_URL）",
         failing_cases([{"name": "x", "url": "https://github.com/x/y/actions/runs/1"}],
                       log_of=read), []),
        ("只读前两个检查的日志（jobs=2）",
         failing_cases(urls("13", "11", "12"), log_of=read),
         ["tests/z.py::w", "tests/a.py::x", "tests/b.py::y"]),
        ("一次日志读不到算没有用例，不影响别的检查",
         failing_cases(urls("14", "13"), log_of=read), ["tests/z.py::w"]),
    ]


def self_test():
    """离线自测：不开网络、不取 token、不调 gh，覆盖读段和读红两条新路径。"""
    bad, total = [], 0

    def check(name, got, want):
        nonlocal total
        total += 1
        if got != want:
            bad.append(f"{name}\n  想要 {want!r}\n  读成 {got!r}")

    for name, body, want_changed, want_verified in _SELF_TEST_BODIES:
        check("读段：" + name, brief(body, limit=10_000), (want_changed, want_verified))
    for name, rollup, want in _SELF_TEST_ROLLUPS:
        check("读红：" + name, [c["name"] for c in failed_checks(rollup)], want)
    for line, want in _SELF_TEST_LOG_LINES:
        found = _CASE_LINE.match(_LOG_STAMP.sub("", line).strip())
        got = next((g for g in found.groups() if g), "").strip() if found else ""
        check("读用例：" + line, got, want)
    for name, got, want in (_self_test_board() + _self_test_patterns() + _self_test_jobs()
                            + _self_test_pulse()):
        check("看板：" + name, got, want)
    for line in bad:
        print("FAIL " + line)
    if bad:
        print(f"self-test: {len(bad)} of {total} failed")
        raise SystemExit(1)
    print(f"self-test: ok ({total} cases, offline)")


def main():
    argv = sys.argv
    if "--self-test" in argv:
        return self_test()
    room = argv[1]
    silent = int(value_of(argv, "--silent-min") or 40)
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
