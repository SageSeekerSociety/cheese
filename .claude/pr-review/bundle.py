#!/usr/bin/env python3
"""bundle.py — what prepare hands the reviewers so they do not go and fetch it themselves.

Two subcommands, both run by pr-review.sh prepare:

  inherit   A delta round (`--since`) whose increment §4 does not re-review — only a merge
            of the base branch, or only tests, docs or copy — inherits the last round's
            conclusion. It writes result.json straight away, the way a tier G change does,
            and no subagent runs. Allowed only when the last round's REVIEW-RESULT.md is on
            this machine, names `--since`, and has no blocker/major: an exempt increment
            cannot fix a blocker, and a conclusion nobody can read is not one to inherit.
            Exit 0 inherited, 1 not (the reason is printed).

  facts     BUNDLE.md, appended to REVIEW.md after PRECOMPUTED.md:
              1. Call slices: every Python function or method, and every TS/Vue function,
                 whose lines the diff touched (or deleted), its source at the head, and
                 every call site of its name in other files, with a few lines around each.
              2. Single-file pyright over the changed Python files, errors only, the ones
                 on changed lines marked.
            Finders read these instead of opening the files; 577 of 1,704 review tool calls
            on twelve merged PRs were the same files read again.

    python3 .claude/pr-review/bundle.py inherit --repo R --base main --since S --head H \\
        --previous DIR/REVIEW-RESULT.md --out DIR/result.json
    python3 .claude/pr-review/bundle.py facts --repo R --tree T --base MB --head H \\
        [--since S] --out DIR/BUNDLE.md [--pyright CMD]
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ready  # noqa: E402  — §4's increment rule lives there; inheriting must not grow a second one
import render  # noqa: E402

#: Caps, so one huge diff cannot turn REVIEW.md into the whole repository.
MAX_FUNCS = 40
MAX_BODY_LINES = 80
MAX_SITES = 12
CONTEXT = 3
#: A name with more hits than this is too common to slice by name (`get`, `run`).
COMMON_HITS = 120
MAX_PYRIGHT = 40
CODE = re.compile(r"\.(py|ts|tsx|js|vue)$")
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
TS_DEF = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:function\*?\s+(?P<f>\w+)|(?:const|let)\s+(?P<c>\w+)\s*(?::[^=]+)?=\s*(?:async\s+)?(?:\([^)]*\)|\w+)\s*(?::[^=]+)?=>)")


def git(repo, *args):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, errors="replace")
    return p.stdout if p.returncode == 0 else None


def show(repo, rev, path):
    return git(repo, "show", f"{rev}:{path}")


# ------------------------------------------------------------------ inherit
def kept_serious(kept):
    """Blockers and majors in a result.json already in the round for this same head — one a
    re-prepare carried over. Inheriting would overwrite it with an empty one."""
    try:
        findings = json.loads(Path(kept).read_text()).get("findings") or []
    except (OSError, ValueError, AttributeError):
        return 0
    return sum(1 for f in findings if isinstance(f, dict) and f.get("severity") in ("blocker", "major"))


def inherit(repo, base, since, head, previous, kept=""):
    """(result dict | None, reason). None means: review the increment as usual."""
    if kept and Path(kept).is_file() and (n := kept_serious(kept)):
        return None, f"这一版已经有一份审查结果，里面有 {n} 条 blocker/major，不拿继承盖掉它"
    prev = Path(previous) if previous else None
    if not prev or not prev.is_file():
        return None, "这台机器上没有上一轮的 REVIEW-RESULT.md，读不到它的结论，照常审增量"
    m = render.CARD_LINE.search(prev.read_text())
    if not m:
        return None, f"{prev} 里没有结论行，照常审增量"
    if not (since.startswith(m.group("head")) or m.group("head").startswith(since)):
        return None, f"上一轮审的是 {m.group('head')[:8]}，不是 {since[:8]}，照常审增量"
    if m.group("serious"):
        return None, f"上一轮有 {m.group('serious')} 条 blocker/major，增量要核它们修了没有"
    inc = ready.increment(repo, base, since, head)
    if inc["needs"]:
        return None, f"增量{inc['detail']}，§4 要审"
    detail = inc["detail"]
    return {
        "reviewed_sha": head,
        "summary": f"增量自 {since[:8]}：{detail}。按 §4 继承上一轮 {since[:8]} 的结论（无 blocker/major），"
                   f"没有起分身；这行说的是增量免审，不是有人读过增量。",
        "candidates_found": 0,
        "candidates_refuted": 0,
        "findings": [],
        "not_covered": [f"{since[:12]}..{head[:12]} 的增量（{detail}）按 §4 免审，继承上一轮结论，没有人读过。"],
    }, detail


# --------------------------------------------------------------- 1. slices
def changed_lines(repo, base, head):
    """{path: (status, new-side lines added or changed, old-side lines removed, new-side
    anchors of pure deletions)}. An anchor is the line a deletion sat after: it places the
    deletion inside a function for the slices, and is not a changed line for pyright."""
    out = {}
    names = git(repo, "diff", "--name-status", "--no-renames", base, head) or ""
    for line in names.splitlines():
        st, _, path = line.partition("\t")
        if CODE.search(path):
            out[path] = (st[:1], set(), set(), set())
    diff = git(repo, "diff", "-U0", "--no-renames", base, head, "--", *out) if out else ""
    path, header = None, False
    for line in (diff or "").splitlines():
        if line.startswith("diff --git "):
            path, header = None, True
        elif header and line.startswith("--- "):
            path = line[6:] if line.startswith("--- a/") else None
        elif header and line.startswith("+++ "):
            # A deleted file's +++ is /dev/null: keep the --- side's path for its hunks.
            path = line[6:] if line.startswith("+++ b/") else path
        elif m := HUNK.match(line):
            # Past the header: a removed line reading `-- x` is `--- x` here, not a file name.
            header = False
            if path not in out:
                continue
            o, oc, n, nc = int(m[1]), int(m[2] or 1), int(m[3]), int(m[4] or 1)
            out[path][2].update(range(o, o + oc))
            if nc:
                out[path][1].update(range(n, n + nc))
            else:
                out[path][3].add(n)
    return out


def py_defs(src):
    """[(qualified name, bare name, first line, last line)] for functions and methods."""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    out = []

    def walk(node, prefix):
        # Every child, not only bodies of defs: a function under `if`, `try` or `with` is
        # still a function a diff can touch.
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                first = min([child.lineno] + [d.lineno for d in child.decorator_list])
                out.append((prefix + child.name, child.name, first, child.end_lineno or child.lineno))
                walk(child, prefix + child.name + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix + child.name + ".")
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


def ts_defs(src):
    """Top-level-ish TS/JS functions: a definition runs until the next one at the same or
    a shallower indent. Approximate on purpose — a slice that is a few lines long is
    still the right place to look, and a parser would need node in the review seat."""
    lines = src.splitlines()
    starts = []
    for i, line in enumerate(lines, 1):
        m = TS_DEF.match(line)
        if m:
            starts.append((i, len(line) - len(line.lstrip()), m["f"] or m["c"]))
    out = []
    for k, (i, indent, name) in enumerate(starts):
        end = len(lines)
        for j, ind2, _ in starts[k + 1:]:
            if ind2 <= indent:
                end = j - 1
                break
        out.append((name, name, i, end))
    return out


def defs_of(path, src):
    if src is None:
        return []
    return py_defs(src) if path.endswith(".py") else ts_defs(src)


def call_sites(repo, head, name, own_path):
    """Lines in other files that use `name` as a word, with CONTEXT lines around each, as
    (path, line, snippet). None when the name is too common to be a useful slice. Raises
    when git grep itself failed: exit 1 is "no match", anything above is not an answer."""
    p = subprocess.run(["git", "-C", str(repo), "grep", "-n", "-I", "-w", "-F", "-e", name, head,
                        "--", "*.py", "*.ts", "*.tsx", "*.js", "*.vue"],
                       capture_output=True, text=True, errors="replace")
    if p.returncode > 1:
        raise RuntimeError(f"git grep {name}: {p.stderr.strip()[:200]}")
    pre = head + ":"
    hits = []
    for line in p.stdout.splitlines():
        line = line[len(pre):] if line.startswith(pre) else line
        path, _, rest = line.partition(":")
        num, _, text = rest.partition(":")
        if path == own_path or not num.isdigit():
            continue
        if re.match(rf"\s*(?:async\s+)?def\s+{re.escape(name)}\b", text):
            continue
        hits.append((path, int(num)))
    if len(hits) > COMMON_HITS:
        return None
    out, cache = [], {}
    for path, num in hits[:MAX_SITES]:
        if path not in cache:
            cache[path] = (show(repo, head, path) or "").splitlines()
        lines = cache[path]
        lo, hi = max(1, num - CONTEXT), min(len(lines), num + CONTEXT)
        snippet = "\n".join(f"{n:>5}{'>' if n == num else ' '} {lines[n - 1]}" for n in range(lo, hi + 1))
        out.append((path, num, snippet))
    return out, len(hits)


def touched(defs, lines):
    hit = [d for d in defs if any(d[2] <= n <= d[3] for n in lines)]
    # An inner function counts on its own; keep its enclosing one only when the change is
    # outside every inner function it holds, so the same lines are not handed out twice.
    return [d for d in hit if not any(o is not d and d[2] <= o[2] and o[3] <= d[3]
                                      and all(not (d[2] <= n <= d[3]) or o[2] <= n <= o[3] for n in lines)
                                      for o in hit)]


def section_slices(repo, base, head):
    out = ["## B1. 调用切片",
           "diff 动到的函数（或删掉的函数）在 head 上的原文，和它在别的文件里的每个调用点（前后各 "
           f"{CONTEXT} 行）。分身先读这里，不够再开文件。"]
    files = changed_lines(repo, base, head)
    picked = []
    for path, (st, new_lines, old_lines, anchors) in sorted(files.items()):
        head_src = show(repo, head, path) if st != "D" else None
        head_defs = defs_of(path, head_src)
        for d in touched(head_defs, new_lines | anchors):
            picked.append((path, d, head_src, "改动"))
        base_defs = defs_of(path, show(repo, base, path)) if st != "A" else []
        still = {d[0] for d in head_defs}
        for d in touched(base_defs, old_lines):
            if d[0] not in still:
                picked.append((path, d, None, "删除"))
    if not picked:
        return out + ["", "diff 没有动到任何函数体（或只改了非代码文件）。"]
    for path, (qual, name, first, last), src, label in picked[:MAX_FUNCS]:
        out.append("")
        out.append(f"### {label} `{qual}`（{path}:{first}）")
        if src is not None:
            body = src.splitlines()[first - 1:last]
            cut = len(body) > MAX_BODY_LINES
            body = body[:MAX_BODY_LINES]
            fence = "python" if path.endswith(".py") else "ts"
            out.append(f"```{fence}\n" + "\n".join(body) + ("\n… （截断，共 %d 行）" % (last - first + 1) if cut else "")
                       + "\n```")
        try:
            sites = call_sites(repo, head, name, path)
        except RuntimeError as exc:
            out.append(f"调用点：没查成（{exc}），分身自己 grep。")
            continue
        if sites is None:
            out.append(f"调用点：`{name}` 这个名字太常见，不按名字切。")
            continue
        found, total = sites
        if not found:
            out.append("调用点：别的文件里没有引用" + ("——删掉之后已经没人用它。" if label == "删除" else "。"))
            continue
        out.append(f"调用点：别的文件 {total} 处" + (f"，列前 {MAX_SITES} 处" if total > MAX_SITES else "") + "：")
        for spath, num, snippet in found:
            out.append(f"- `{spath}:{num}`\n```\n{snippet}\n```")
    if len(picked) > MAX_FUNCS:
        out.append(f"\n还有 {len(picked) - MAX_FUNCS} 个函数没列，分身自己读。")
    return out


# --------------------------------------------------------------- 2. pyright
def find_pyright(repo, given):
    if given:
        return shlex.split(given)
    # Absolute: pyright runs with cwd set to the review tree, where a relative path is gone.
    cand = (Path(repo) / "backend/.venv/bin/pyright").resolve()
    if cand.exists():
        return [str(cand)]
    found = shutil.which("pyright")
    return [found] if found else None


def section_pyright(repo, tree, base, head, given=None):
    out = ["## B2. 单文件 pyright",
           "改动的 Python 文件逐个跑 pyright，只列 error；标 ⚠ 的落在改动行上。"]
    files = {p: v for p, v in changed_lines(repo, base, head).items() if p.endswith(".py") and v[0] != "D"}
    if not files:
        return out + ["", "没有改动的 Python 文件。"]
    cmd = find_pyright(repo, given)
    if not cmd:
        return out + ["", "这台机器上没有 pyright（backend/.venv 和 PATH 都没有），没跑；类型检查交给 CI。"]
    venv = Path(repo) / "backend/.venv"
    if venv.exists() and (Path(tree) / "backend").is_dir() and not (Path(tree) / "backend/.venv").exists():
        try:
            (Path(tree) / "backend/.venv").symlink_to(venv)
        except OSError:
            pass
    # backend/ has its own pyproject [tool.pyright]; everything else runs from the root.
    groups = {}
    for p in files:
        root = "backend" if p.startswith("backend/") else ""
        groups.setdefault(root, []).append(p)
    errors, ran = [], []
    for root, paths in groups.items():
        cwd = Path(tree) / root if root else Path(tree)
        rel = [p[len(root) + 1:] if root else p for p in paths]
        try:
            proc = subprocess.run([*cmd, "--outputjson", *rel], cwd=cwd, capture_output=True, text=True, errors="replace", timeout=600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            out.append(f"- `{root or '.'}` 下没跑成：{exc}")
            continue
        try:
            report = json.loads(proc.stdout)
        except ValueError:
            out.append(f"- `{root or '.'}` 下 pyright 没给出 JSON（退出 {proc.returncode}）："
                       + (proc.stderr or proc.stdout).strip()[:200])
            continue
        ran += paths
        for d in report.get("generalDiagnostics", []):
            if d.get("severity") != "error":
                continue
            fpath = os.path.relpath(d.get("file", ""), Path(tree).resolve()) if os.path.isabs(d.get("file", "")) else d.get("file", "")
            line = d.get("range", {}).get("start", {}).get("line", -1) + 1
            mark = "⚠ " if line in files.get(fpath, ("", set(), set(), set()))[1] else ""
            rule = f"（{d['rule']}）" if d.get("rule") else ""
            errors.append(f"- {mark}`{fpath}:{line}` {d.get('message', '').splitlines()[0]}{rule}")
    out.append("")
    out.append(f"跑了 {len(ran)} 个文件，{len(errors)} 条 error。")
    out += errors[:MAX_PYRIGHT]
    if len(errors) > MAX_PYRIGHT:
        out.append(f"… 还有 {len(errors) - MAX_PYRIGHT} 条没列。")
    return out


def facts(repo, tree, base, head, pyright=None):
    secs = []
    for fn in (lambda: section_slices(repo, base, head),
               lambda: section_pyright(repo, tree, base, head, pyright)):
        try:
            secs.append("\n".join(fn()))
        except Exception as exc:  # a section that breaks must not take the review down
            secs.append(f"（这一节没算出来：{type(exc).__name__}: {exc}）")
    return ("## Bundle\n\n随包给分身的原文：改动函数、它们的调用点、单文件类型检查。"
            "分身不要再自己去读这些文件；这里没有的才去读。\n\n" + "\n\n".join(secs) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("inherit")
    a.add_argument("--repo", required=True)
    a.add_argument("--base", default="", help="the PR's base branch name (main)")
    a.add_argument("--since", required=True)
    a.add_argument("--head", required=True)
    a.add_argument("--previous", default="")
    a.add_argument("--kept", default="", help="result.json already in this round, if any")
    a.add_argument("--out", required=True)
    f = sub.add_parser("facts")
    f.add_argument("--repo", required=True)
    f.add_argument("--tree")
    f.add_argument("--base", required=True, help="merge base, or --since for a delta round")
    f.add_argument("--head", required=True)
    f.add_argument("--pyright", help="pyright command (default: backend/.venv, then PATH)")
    f.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "inherit":
        result, why = inherit(args.repo, args.base, args.since, args.head, args.previous, args.kept)
        if result is None:
            print(f"bundle: 不继承：{why}", file=sys.stderr)
            return 1
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"bundle: 增量自 {args.since[:8]} {why}，继承上一轮结论，不起分身", file=sys.stderr)
        return 0
    Path(args.out).write_text(facts(args.repo, args.tree or args.repo, args.base, args.head, args.pyright))
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
