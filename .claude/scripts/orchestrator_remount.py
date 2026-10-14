#!/usr/bin/env python3
"""Remount the dispatcher's two background loops after a machine swap, in one command.

  OKCHEESE_USER=... OKCHEESE_PASS=... python3 .claude/scripts/orchestrator_remount.py \
      [--wd /home/cheese/wd] [--dry-run]
  python3 .claude/scripts/orchestrator_remount.py --self-test      (offline: no token, no network)

《总调度工作流 v2》§9 S7: 取脚本、起循环、checkout、带回 `weekly.json`、核 1/1.
In order, this run counts what is already up, takes the two loop scripts out of the
project library and checks their digests against the ones pinned here, rebuilds
`weekly.json` from the platform so a swap cannot re-fire this week's weekly
retrospective, starts whatever is missing, reads the count again and expects 1/1 per
loop, and finally puts the default-branch copy of the repo on disk with
`cheese checkout`. Nothing else: the loops are the watchdog's own work, and this only
puts them back.

A loop is counted by its script name in a `python` invocation's arguments, parsed out of
`ps` in Python rather than grepped, so the counting process cannot count itself. One of
each is the contract — a second timer would ping #1343 twice a round, a second watchdog
would both patch the same heartbeat block. This never kills a duplicate: it reports
`timer 2/1` and stops, because killing a process is not what "remount" was asked to do.

The digests are the point of the fetch, not a formality. `SOURCE_SHA` is the one place
that says which library copy is the reviewed one; the watchdog manual points here rather
than carrying its own hash. A mismatch stops the run before anything is started, and the
answer is to read the new copy and change `SOURCE_SHA`, not to skip the check.

`weekly.json` is the timer's own one-shot guard (`timer.py` writes `{"week": "%G-W%V"}`
when it fires the weekly retrospective on Monday 01:00–01:59 UTC). A swap inside that
hour would otherwise start a timer with no guard and ping a second time. This rebuilds it
in the only safe direction: write the current week when the platform already shows this
week's 《总调度周深度复盘 …》, leave the file alone otherwise. It never deletes and never
downgrades a week — a file that claims the fire happened is what stops a duplicate, and
clearing it is how one gets written.

That join is by creation time, not by the week in the document's title. The one weekly
retrospective in the library is titled 《总调度周深度复盘 2026-W41（10-06 至 10-13，UTC）》
and was created 2026-10-14T02:12Z, inside ISO week 2026-W42 — the week the timer's key
would carry. Matching the title's week against the timer's key would never line up.

Exit status: 0 loops are up (started now or already were) · 2 the command line or the
environment is wrong · 3 a fetch, a digest or the platform refused · 4 the count is not
1/1, including a loop that did not come up. Exit 3 leaves the loops as they were: the
run stops before starting anything.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

from orchestrator_sweep import get, login

#: Where the loops live. Not `~/wd`: on a work machine `~` is the session's own home
#: directory and is rebuilt per session, while this directory survives until the machine
#: itself is reclaimed (the watchdog manual's reason, and the one that cost a 7-hour gap).
DEFAULT_WD = "/home/cheese/wd"

#: The library name and the digest of the copy this script will run, per loop. The
#: library cannot overwrite a name, so the timer's current copy is filed as `timer(2).py`
#: and lands on disk as `timer.py`; taking `timer.py` out of the library instead gets the
#: old copy with no weekly-retrospective step in it. The watchdog manual points here for
#: the digests — one place, not two.
SOURCES = {
    "watchdog": ("dispatcher-watchdog.py",
                 "892617ddae9bd6fcf19d3cd9e9908f582c1c16e4cafc90c8c522187a1766b9d9"),
    "timer": ("timer(2).py",
              "4f01cb04c1f2d1faf4ce53f1ae7feaa5852f67060107124dbb601823d4cb300f"),
}
LOOPS = tuple(SOURCES)  # ("watchdog", "timer")

#: A 《总调度周深度复盘 …》 row is the product of the timer's weekly fire. The 3-hour
#: retrospectives are 总调度复盘 … and are a different thing.
WEEKLY_RETRO_TITLE = "总调度周深度复盘"

#: The name a loop's argv has to carry to be counted as that loop. Anchored on the script
#: name alone so `python3 -u /home/cheese/wd/timer.py` and a bare `python3 -u timer.py`
#: are both one timer, and a `vim timer.py` or a `grep timer.py` is neither.
LOOP_ARG = re.compile(r"(?:^|[\s/])(timer|watchdog)\.py(?:\s|$)")

PS_TIMEOUT = 15
#: How long to wait between starting a loop and counting again.
SETTLE_SECONDS = 3


# ------------------------------------------------------------------ the machine

def list_processes(run=None):
    """`ps`'s `pid,args` rows as a list of (pid, args). `run` is the one seam the
    self-test needs here: the real thing shells out, the tests hand over a listing."""
    out = (run or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, timeout=PS_TIMEOUT).stdout))(
        ["ps", "-ww", "-eo", "pid=,args="])
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, args = line.partition(" ")
        rows.append((pid.strip(), args.strip()))
    return rows


def count_loops(rows):
    """How many of each loop the listing shows: `{"timer": n, "watchdog": n}`.

    Only a `python` invocation counts. The name alone is not enough — an editor or a
    grep of the script is not the loop, and counting it is how a remount reports 1/1
    over nothing.
    """
    found = {name: 0 for name in LOOPS}
    for _pid, args in rows:
        if "python" not in args:
            continue
        m = LOOP_ARG.search(args)
        if m and m.group(1) in found:
            found[m.group(1)] += 1
    return found


# ------------------------------------------------------------------ the library copy

def library_get(name, dest, run=None):
    """`cheese library get <name> --out <dest>`'s exit status and output.

    The CLI uses whatever platform credentials the machine already has — the loops'
    dev-account password is not involved and is never handed to it.
    """
    proc = (run or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, timeout=120)))([
            "cheese", "library", "get", name, "--out", dest])
    out = (getattr(proc, "stdout", "") or "") + (getattr(proc, "stderr", "") or "")
    return proc.returncode, out.strip()


def digest_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_loops(wd, run=None):
    """Take both scripts out of the library into `wd` and check their digests.

    Returns (ok, report). `ok` is False on a fetch that failed or a digest that is not
    the pinned one; in both cases the report says which loop and why, and the caller
    stops before starting anything.
    """
    lines, ok = [], True
    for name in LOOPS:
        source, want = SOURCES[name]
        dest = os.path.join(wd, name + ".py")
        code, out = library_get(source, dest, run=run)
        if code != 0:
            ok = False
            lines.append(f"{name} 取不到（{source} → {dest}）：" + (out or f"exit {code}"))
            continue
        if not os.path.exists(dest):
            ok = False
            lines.append(f"{name} 取完文件不在：{dest}")
            continue
        got = digest_of(dest)
        if got != want:
            ok = False
            lines.append(f"{name} 摘要不对：核对的是 {want[:12]}…，取回来的是 {got[:12]}…"
                         f"（源 {source}）。读过新的那份再改 SOURCES，不要跳过核对。")
            continue
        lines.append(f"{name} 已取回并核对（{source} → {dest}，{got[:12]}…）")
    return ok, "\n".join(lines)


# ------------------------------------------------------------------ weekly.json

_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)


def _created(row):
    """A document row's creation instant, or `_EARLIEST` when it does not say."""
    try:
        raw = (row.get("created_at") or "").replace("Z", "+00:00")
        when = datetime.fromisoformat(raw)
    except ValueError:
        return _EARLIEST
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def iso_week_start(when):
    """Monday 00:00 UTC of the ISO week `when` is in."""
    monday = when.date().fromordinal(when.date().toordinal() - when.date().weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)


def weekly_key(when):
    """The key `timer.py` writes and compares: `%G-W%V`, ISO week and year."""
    return when.strftime("%G-W%V")


def weekly_retro_done(docs, when):
    """Is this week's 《总调度周深度复盘 …》 already in `docs`?

    By creation time inside the current ISO week, not by the week in the title: the
    title's week labels the period the retrospective covers, which is the week that just
    ended, while the timer's key is the week it fires in. The library's one weekly
    retrospective is titled 2026-W41 and was created inside 2026-W42, so a title match
    against the timer's key would never line up.
    """
    floor = iso_week_start(when)
    for row in docs if isinstance(docs, list) else []:
        if not isinstance(row, dict):
            continue
        if not str(row.get("title") or "").startswith(WEEKLY_RETRO_TITLE):
            continue
        created = _created(row)
        if created != _EARLIEST and created >= floor:
            return True
    return False


def read_weekly(path):
    """The week the file claims, or None when there is no readable one."""
    try:
        with open(path) as f:
            week = json.load(f).get("week")
    except (OSError, ValueError):
        return None
    return week if isinstance(week, str) and week else None


def _write_weekly(path, week, write):
    payload = json.dumps({"week": week}, ensure_ascii=False)
    if write is not None:
        write(path, payload)
        return
    with open(path, "w") as f:
        f.write(payload)


def rebuild_weekly(wd, docs, when, write=None):
    """Bring `weekly.json` back from what the platform shows. Always (True, report).

    One direction only. When the platform already shows this week's weekly
    retrospective the file is written with the timer's key; in every other case the file
    is left exactly as it is. A file that already claims a week is never cleared and
    never moved backwards: that claim is what stands between a swapped machine and a
    second ping, so clearing it is how a duplicate gets written. None of the three
    outcomes is a failure — "this week's fire has not happened yet" is a normal state,
    and the loops start either way.
    """
    path = os.path.join(wd, "weekly.json")
    week = weekly_key(when)
    have = read_weekly(path)
    if have == week:
        return True, f"weekly.json 已写着 {week}，不动"
    if not weekly_retro_done(docs, when):
        tail = f"，原文件写的是 {have}，保留" if have else "，也还没有文件"
        return True, f"weekly.json 未写：平台上还没有这一周（{week}）的周深度复盘{tail}"
    _write_weekly(path, week, write)
    if have:
        return True, f"weekly.json 把 {have} 换成 {week}（平台上已有这一周的周深度复盘）"
    return True, f"weekly.json 已写 {week}（平台上已有这一周的周深度复盘）"


# ------------------------------------------------------------------ the loops

def start_loop(wd, name, password, run=None):
    """Start one loop detached from this session, with the password on its stdin.

    Detached is the point: `setsid`/`start_new_session` is what keeps it alive when the
    session that ran this goes away, which is the whole reason the manual insists on it.
    The password goes to the child's stdin and nowhere else — not argv, not a file, not
    this run's output.
    """
    script = os.path.join(wd, name + ".py")
    log = open(os.path.join(wd, "boot.log"), "a")
    try:
        if run is not None:
            return run([script, name, password])
        proc = subprocess.Popen(
            ["python3", "-u", script],
            cwd=wd, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True)
        proc.stdin.write((password + "\n").encode())
        proc.stdin.close()
        return proc.pid
    finally:
        log.close()


def which_missing(counts):
    """Which loops still need starting: every one that is not already up."""
    return [name for name in LOOPS if counts.get(name, 0) == 0]


def check_counts(counts):
    """(ok, report) for the 1/1 contract after whatever this run did."""
    ok = all(counts.get(name) == 1 for name in LOOPS)
    report = "、".join(f"{name} {counts.get(name, 0)}/1" for name in LOOPS)
    return ok, report


def checkout_main(run=None):
    """`cheese checkout` — the default-branch copy at ~/.cheese/checkout."""
    proc = (run or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, timeout=600)))(
        ["cheese", "checkout"])
    out = (getattr(proc, "stdout", "") or "").strip()
    return proc.returncode, out


# ------------------------------------------------------------------ the run

def list_docs(tok, reader=None):
    """Every document row in the project library. `reader` is the self-test's seam.

    Paged the way the platform answers — the same loop `orchestrator_metrics.py` walks —
    because one page of 50 is not the library and a weekly retrospective written early in
    the week is exactly what falls off the end of a short read.
    """
    read = reader or get
    rows = []
    for project in (read(tok, "/projects") or {}).get("data") or []:
        cursor = None
        for _ in range(20):
            page = read(tok, f"/projects/{project['id']}/documents?limit=200"
                            + (f"&cursor={cursor}" if cursor else "")) or {}
            rows += page.get("data") or []
            cursor = page.get("next")
            if not cursor:
                break
    return rows


def remount(wd, password, docs, when, dry_run=False, deps=None):
    """One remount. (exit code, the report to print).

    `deps` carries the five things that touch the machine or the platform, each one the
    seam a self-test replaces: `list`/`get`/`start`/`checkout`/`write`.
    """
    d = {"list": None, "get": None, "start": None, "checkout": None, "write": None}
    d.update(deps or {})
    report = []

    rows = (d["list"] or list_processes)()
    before = count_loops(rows)
    report.append("在跑：" + "、".join(f"{n} {before.get(n, 0)}" for n in LOOPS))

    if dry_run:
        missing = which_missing(before)
        report.append("会取回并核对：" + "、".join(SOURCES[n][0] for n in LOOPS))
        report.append("会补 weekly.json（平台已有这一周的周深度复盘时才写）")
        report.append("会起：" + ("、".join(missing) if missing else "无，都在跑"))
        report.append("会核 1/1，并跑 cheese checkout")
        return 0, "\n".join(report)

    ok, line = fetch_loops(wd, run=d["get"])
    report.append(line)
    if not ok:
        report.append("摘要或取回没对上，两个循环一个也没起")
        return 3, "\n".join(report)

    ok, line = rebuild_weekly(wd, docs, when, write=d["write"])
    report.append(line)

    missing = which_missing(before)
    for name in missing:
        pid = start_loop(wd, name, password, run=d["start"])
        report.append(f"{name} 已起（pid {pid}）")
    if not missing:
        report.append("两个都在跑，没有要起的")

    if missing and d["start"] is None:
        # Give the children a moment to show up in `ps` before counting again. The
        # watchdog manual's one-shot waits 6; 3 is enough for `python3 -u` to be
        # scheduled, and the self-test replaces this branch's caller so it never waits.
        time.sleep(SETTLE_SECONDS)
    after = count_loops((d["list"] or list_processes)())
    ok, line = check_counts(after)
    report.append("核 1/1：" + line)
    if not ok:
        report.append("数目不对：起完不是各 1 个。多出来的没有杀——先看清是哪两个，再动手")
        return 4, "\n".join(report)

    code, out = checkout_main(run=d["checkout"])
    if code == 0:
        report.append("cheese checkout " + (out or f"exit {code}"))
    else:
        report.append(f"cheese checkout 失败（exit {code}）：" + (out or ""))
    return (0 if code == 0 else 4), "\n".join(report)


def main(argv):
    p = argparse.ArgumentParser(
        prog="orchestrator_remount.py",
        description="换机重挂：取脚本、起循环、checkout、带回 weekly.json、核 1/1")
    p.add_argument("--wd", default=DEFAULT_WD)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args(argv)

    if args.self_test:
        return self_test()

    password = os.environ.get("OKCHEESE_PASS")
    if not password:
        print("OKCHEESE_PASS 没设；密码只从环境变量读，不进 argv、不落文件", file=sys.stderr)
        return 2
    if os.environ.get("OKCHEESE_USER") is None:
        print("OKCHEESE_USER 没设", file=sys.stderr)
        return 2

    try:
        os.makedirs(args.wd, exist_ok=True)
    except OSError as e:
        print(f"建不了 {args.wd}：{e}", file=sys.stderr)
        return 2

    docs = []
    if not args.dry_run:
        try:
            docs = list_docs(login())
        except Exception as e:
            print(f"平台读不到文档列表，weekly.json 不动：{e!r}", file=sys.stderr)
            docs = []

    code, report = remount(args.wd, password, docs,
                           datetime.now(timezone.utc), dry_run=args.dry_run)
    print(report)
    return code


def self_test():
    """(name, got, want) for everything that needs no machine and no platform.

    The cases drive `remount` with every external call replaced, so the exit codes the
    docstring promises are covered without starting anything: a digest that is not the
    pinned one must leave both loops unstarted, and a count that is not 1/1 must come
    back as 4 even when everything else went well.
    """
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    def ps_row(pid, args):
        return (pid, args)

    # -- 数清在跑的循环 ------------------------------------------------------
    rows = [
        ps_row("101", "python3 -u /home/cheese/wd/timer.py"),
        ps_row("102", "python3 -u watchdog.py"),
        ps_row("103", "vim timer.py"),
        ps_row("104", "grep timer.py boot.log"),
        ps_row("105", "python3 orchestrator_remount.py"),
        ps_row("106", "sshd: cheese"),
    ]
    check("两个循环各数到一个", count_loops(rows), {"watchdog": 1, "timer": 1})
    check("没有在跑时是 0，不是缺键", count_loops([]), {"watchdog": 0, "timer": 0})
    check("编辑器和 grep 里的名字不算循环",
          count_loops([ps_row("1", "vim timer.py"), ps_row("2", "grep watchdog.py boot.log")]),
          {"watchdog": 0, "timer": 0})
    check("本脚本自己的 argv 不算循环",
          count_loops([ps_row("1", "python3 orchestrator_remount.py")]),
          {"watchdog": 0, "timer": 0})
    check("一个 python 里同名两个只算一个循环，按脚本名计",
          count_loops([ps_row("1", "python3 -u timer.py --wd /home/cheese/wd")]),
          {"watchdog": 0, "timer": 1})
    check("跑着两个 timer 是 2，会被 1/1 拦下",
          check_counts({"watchdog": 1, "timer": 2}),
          (False, "watchdog 1/1、timer 2/1"))
    check("各一个是 1/1",
          check_counts({"watchdog": 1, "timer": 1}),
          (True, "watchdog 1/1、timer 1/1"))
    check("缺一个也不是 1/1",
          check_counts({"watchdog": 0, "timer": 1}),
          (False, "watchdog 0/1、timer 1/1"))
    check("都在跑时没有要起的", which_missing({"watchdog": 1, "timer": 1}), [])
    check("两个都缺时两个都要起", which_missing({"watchdog": 0, "timer": 0}),
          ["watchdog", "timer"])

    # -- 周次与 weekly.json --------------------------------------------------
    when = datetime(2026, 10, 14, 2, 30, tzinfo=timezone.utc)  # Wednesday, ISO 2026-W42
    check("周次按 ISO，和 timer.py 写的是同一个键", weekly_key(when), "2026-W42")
    check("周一 01:00 那一小时也在同一周",
          weekly_key(datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc)), "2026-W42")
    check("周起点是那一周的周一 00:00 UTC",
          iso_week_start(when), datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc))
    check("周一凌晨算自己那一周，不是上一周",
          iso_week_start(datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc)),
          datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc))

    weekly_doc = {"title": "总调度周深度复盘 2026-W41（10-06 至 10-13，UTC）",
                  "created_at": "2026-10-14T02:12:54Z"}
    three_hour = {"title": "总调度复盘 10-14 07:15–10:15（UTC）",
                  "created_at": "2026-10-14T10:18:56Z"}
    check("这一周里写出的周深度复盘算已经点过",
          weekly_retro_done([weekly_doc], when), True)
    check("标题里的周次不是判据（它写的是覆盖的那一周）",
          weekly_retro_done([weekly_doc], when), True)
    check("3 小时复盘不算周深度复盘",
          weekly_retro_done([three_hour], when), False)
    check("上一周写的周深度复盘不算这一周",
          weekly_retro_done([dict(weekly_doc, created_at="2026-10-07T02:12:54Z")], when),
          False)
    check("认不出的时间戳不算", weekly_retro_done([dict(weekly_doc, created_at="x")], when),
          False)
    check("没有文档时是 False", weekly_retro_done([], when), False)

    def drive_weekly(path_week, docs, write_spy=None):
        """(what the file says after, what got written) for one rebuild."""
        written = {}

        def write(path, payload):
            written[path] = json.loads(payload)

        state = {"week": path_week} if path_week is not None else None

        def read(_path):
            return state["week"] if state else None

        real_read, real_write = globals()["read_weekly"], globals()["_write_weekly"]
        globals()["read_weekly"] = read
        try:
            ok, line = rebuild_weekly("/wd", docs, when,
                                      write=write if write_spy is None else write_spy)
        finally:
            globals()["read_weekly"] = real_read
            globals()["_write_weekly"] = real_write
        return ok, line, written

    ok, line, written = drive_weekly(None, [weekly_doc])
    check("平台上已有这一周的周复盘时，补出 weekly.json", (ok, written),
          (True, {"/wd/weekly.json": {"week": "2026-W42"}}))
    check("补写时说清是按平台上的哪一份", "2026-W42" in line and "周深度复盘" in line, True)

    ok, line, written = drive_weekly("2026-W42", [weekly_doc])
    check("文件已经写着这一周就不再写", (ok, written), (True, {}))

    ok, line, written = drive_weekly("2026-W41", [weekly_doc])
    check("上一周的记录被换成这一周", written, {"/wd/weekly.json": {"week": "2026-W42"}})

    ok, line, written = drive_weekly(None, [])
    check("平台上还没有周复盘时不写，也不算失败", (ok, written), (True, {}))

    ok, line, written = drive_weekly("2026-W41", [])
    check("平台上没有时保留原文件，不降级不删除", (ok, written), (True, {}))
    check("保留时说清原文件写的是哪一周", "保留" in line and "2026-W41" in line, True)

    # -- 一次完整的重挂 ------------------------------------------------------
    def drive(count_before, count_after, digest_ok=True, fetch_code=0,
              docs=None, checkout_code=0, password="pw"):
        """(exit code, report, what each stub was asked) for one `remount`.

        `digest_ok` decides which digest the fetch stub's file has and which one
        `SOURCES` pins, so the comparison being tested is the real one and not a
        lambda standing in for it. When it is False the file's digest is the wrong
        one and the run must stop before starting anything.
        """
        seen = {"started": [], "checkout": 0, "fetched": []}
        state = {"n": 0}
        import hashlib as _hashlib
        import tempfile
        body = b"loop\n" if digest_ok else b"tampered\n"
        # What `SOURCES` pins is the digest of the reviewed copy. `digest_ok` decides
        # what lands on disk: either it is that copy, or it is another one.
        pinned = _hashlib.sha256(b"loop\n").hexdigest()

        def listing():
            state["n"] += 1
            return count_before if state["n"] == 1 else count_after

        def get_run(argv):
            seen["fetched"].append(argv[3])
            if fetch_code:
                return type("P", (), {"returncode": fetch_code,
                                      "stdout": "no such file", "stderr": ""})()
            with open(argv[argv.index("--out") + 1], "wb") as f:
                f.write(body)
            return type("P", (), {"returncode": 0, "stdout": "已写入", "stderr": ""})()

        def start(argv):
            seen["started"].append(argv[1])
            return 1234

        def checkout(argv):
            seen["checkout"] += 1
            return type("P", (), {"returncode": checkout_code,
                                  "stdout": "/home/cheese/.cheese/checkout", "stderr": ""})()

        real_sources = globals()["SOURCES"]
        tmp = tempfile.mkdtemp()
        try:
            globals()["SOURCES"] = {name: (real_sources[name][0], pinned) for name in LOOPS}
            code, report = remount(
                tmp, password, docs if docs is not None else [weekly_doc],
                when, deps={"list": listing, "get": get_run, "start": start,
                            "checkout": checkout, "write": lambda p, s: None})
        finally:
            globals()["SOURCES"] = real_sources
        return code, report, seen

    both = [ps_row("1", "python3 -u timer.py"), ps_row("2", "python3 -u watchdog.py")]
    none = []

    code, report, seen = drive(none, both)
    check("两个都缺时都起，退出 0", code, 0)
    check("起的是缺的那两个", sorted(seen["started"]), ["timer", "watchdog"])
    check("起了之后核 1/1", "timer 1/1" in report and "watchdog 1/1" in report, True)
    check("最后跑 cheese checkout", seen["checkout"], 1)
    check("报告先说在跑几个", report.splitlines()[0].startswith("在跑："), True)

    code, report, seen = drive(both, both)
    check("两个都在跑时不再起，退出 0", (code, seen["started"]), (0, []))
    check("都在跑时也照跑 checkout", seen["checkout"], 1)

    code, report, seen = drive(none, [ps_row("1", "python3 -u timer.py")])
    check("起了还没起来是 4，不是 0", code, 4)
    check("没起来时把 1/1 打出来", "timer 1/1" in report and "watchdog 0/1" in report, True)

    code, report, seen = drive(both, [ps_row("1", "python3 -u timer.py"),
                                      ps_row("2", "python3 -u watchdog.py"),
                                      ps_row("3", "python3 -u watchdog.py")])
    check("多出一个也是 4", code, 4)
    check("多出来时说清是哪几个、且没杀", "watchdog 2/1" in report and "没有杀" in report, True)

    code, report, seen = drive(none, both, digest_ok=False)
    check("摘要不对是 3", code, 3)
    check("摘要不对时一个也没起", seen["started"], [])
    check("摘要不对时不跑 checkout", seen["checkout"], 0)
    check("摘要不对时报出两边的摘要", "摘要不对" in report, True)

    code, report, seen = drive(none, both, fetch_code=1)
    check("取不回也是 3", code, 3)
    check("取不回时一个也没起", seen["started"], [])
    check("取不回时说清是哪一份", "watchdog 取不到" in report or "timer 取不到" in report, True)

    code, report, seen = drive(none, both, checkout_code=1)
    check("checkout 失败也是 4", code, 4)
    check("checkout 失败时 1/1 已经核过", "timer 1/1" in report, True)

    code, report, seen = drive(none, both, docs=[])
    check("平台上没有周复盘时照样把循环挂起来",
          (code, sorted(seen["started"])), (0, ["timer", "watchdog"]))
    check("没写 weekly.json 时说清不是失败", "weekly.json 未写" in report, True)

    # -- 密码不外泄 ----------------------------------------------------------
    code, report, seen = drive(none, both, password="sekret-pw")
    check("报告里不出现密码", "sekret-pw" in report, False)

    for f in failures:
        print("FAIL", f)
    print("self-test:", "ok" if not failures else f"{len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
