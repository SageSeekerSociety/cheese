#!/usr/bin/env python3
"""precompute.py — facts about a PR that a reviewer would otherwise burn subagents on.

pr-review.sh prepare runs this and appends its PRECOMPUTED.md to REVIEW.md. Four
sections, each a thing a script finds faster and more exactly than a model:

  1. ci:fast — what already passed (do not re-run it) and what did not run (aim here).
  2. Symbols — public Python/TS symbols the diff added, removed or changed, where the
     whole repo references them, where tests patch them, and exports that the deletion
     left with one reference (their own definition): dead code the diff made.
  3. Trial merge — when the PR adds a repo-wide guard test (_KNOWN/_BASELINE) or its body
     names other PRs, merge them with the latest main in a scratch tree and run the new
     tests there. Two PRs that are each green alone can fail together.
  4. prove-regression --auto and recompute-pr-claims on the PR's own tree and body.

Nothing here decides anything: it prints facts and a section that cannot be computed
says so. Exit 0 whenever PRECOMPUTED.md was written.

    python3 .claude/pr-review/precompute.py --repo R --tree T --base B --head H \\
        --pr N --pr-body pr.md --scripts .claude/scripts --out PRECOMPUTED.md
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_SYMBOLS = 300
MAX_LOCS = 8
COMMON_HITS = 200
MAX_REMOVED = 600
TS_EXPORT = re.compile(
    r"^export\s+(?:declare\s+)?(?:default\s+)?(?:abstract\s+)?(?:async\s+)?"
    r"(?:type|interface|const|let|function\*?|class|enum)\s+(\w+)", re.M)
TEST_PATH = re.compile(r"(^|/)(tests?/|test_[^/]*$|[^/]*\.(test|spec)\.[jt]sx?$)")
GUARD_WORDS = re.compile(r"^\s*[A-Z0-9_]*(?:KNOWN|BASELINE)[A-Z0-9_]*\s*(?::[^=\n]*)?=", re.M)
PATCHERS = re.compile(r"setattr|monkeypatch|mocker\.|\bpatch(?:\.object)?\(|vi\.(?:mock|spyOn)|jest\.(?:mock|spyOn)")
PR_MENTION = re.compile(r"(?<![\w/])#(\d{3,6})\b")


def git(repo, *args, check=True, env=None):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(args[:3])}: {p.stderr.strip()[:300]}")
    return p.stdout


def show(repo, rev, path):
    p = subprocess.run(["git", "-C", str(repo), "show", f"{rev}:{path}"], capture_output=True)
    return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None


# ---------------------------------------------------------------- 1. ci:fast
def section_ci(repo, tree, report, head=None):
    out = ["## 1. ci:fast 报告"]
    cands = [Path(report)] if report else []
    for d in (tree, repo):
        try:
            cands.append(Path(d) / git(d, "rev-parse", "--git-path", "ci-fast-report.json").strip())
        except RuntimeError:
            pass
    path = next((c for c in cands if c.is_file()), None)
    if path is None:
        return out + ["没有找到 ci-fast-report.json（审查机上没跑过 ci:fast）。没有已 pass 的检查可以信任，"
                      "所有检查都按没跑过对待。"]
    try:
        rep = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return out + [f"{path} 读不出来（{exc}），按没跑过对待。"]
    rep_head = str(rep.get("head") or "")
    if head and not (rep_head and (head.startswith(rep_head) or rep_head.startswith(head))):
        return out + [f"`{path}` 是别的提交（{rep_head[:12] or '没写'}）的报告，不是钉住的 {head[:12]}，不采信。"
                      "所有检查都按没跑过对待。"]
    ran = rep.get("ran") or []
    passed = [r["id"] for r in ran if r.get("result") == "pass"]
    other = [r for r in ran if r.get("result") not in ("pass", "not_applicable")]
    not_run = rep.get("not_run") or []
    out.append(f"报告：`{path}`，状态 {rep.get('status')}，档 {rep.get('tier')}。")
    out.append("")
    out.append(f"- **已 pass（{len(passed)} 项，分身不要重跑）**：" + (", ".join(f"`{i}`" for i in passed) or "无"))
    out.append(f"- **未 pass 的已跑项（{len(other)}）**：" + (
        "; ".join(f"`{r['id']}` {r.get('result')} {r.get('reason', '')}".strip() for r in other) or "无"))
    out.append(f"- **没跑（{len(not_run)} 项，审查靶心）**：" + (
        "; ".join(f"`{r['id']}`（{r.get('reason', '')}）" for r in not_run) or "无"))
    return out


# --------------------------------------------------------------- 2. symbols
def py_symbols(src):
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return {}
    out = {}

    def ok(n):
        return not n.startswith("_") or bool(re.fullmatch(r"_[A-Z][A-Z0-9_]+", n))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and ok(node.name):
            out[node.name] = ast.dump(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and ok(t.id):
                    out[t.id] = ast.dump(node)
    return out


def ts_symbols(src):
    ms = list(TS_EXPORT.finditer(src))
    out = {}
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(src)
        out[m.group(1)] = re.sub(r"\s+", " ", src[m.start():end]).strip()
    return out


def symbols(path, src):
    if src is None:
        return {}
    if path.endswith(".py"):
        return py_symbols(src)
    if re.search(r"\.(ts|tsx|vue)$", path):
        return ts_symbols(src)
    return {}


def refs(repo, rev, name):
    p = subprocess.run(["git", "-C", str(repo), "grep", "-n", "-I", "-w", "-F", "-e", name, rev],
                       capture_output=True, text=True)
    pre = rev + ":"
    return [(l[len(pre):] if l.startswith(pre) else l) for l in p.stdout.splitlines()]


DEFINITION = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|const|let|type|interface|enum)\s|^\s*[A-Za-z_]\w*\s*(?::[^=]+)?=")


def count_refs(repo, rev, names):
    """Matches per name in one git grep: -o prints every whole-word hit on its own line."""
    if not names:
        return {}
    pat = "\n".join(names) + "\n"
    p = subprocess.run(["git", "-C", str(repo), "grep", "-o", "-h", "-I", "-w", "-F", "-f", "/dev/stdin", rev],
                       input=pat, capture_output=True, text=True)
    counts = dict.fromkeys(names, 0)
    for w in p.stdout.split():
        if w in counts:
            counts[w] += 1
    return counts


def removed_identifiers(repo, base, head):
    try:
        diff = git(repo, "diff", "-U0", "--no-renames", base, head)
    except RuntimeError:
        return []
    names = {}
    for line in diff.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            for w in re.findall(r"[A-Za-z_]\w{3,}", line):
                names[w] = None
    return list(names)


def section_symbols(repo, base, head):
    out = ["## 2. 公开符号与引用"]
    try:
        names = git(repo, "diff", "--name-status", "--no-renames", base, head).splitlines()
    except RuntimeError as exc:
        return out + [f"算不出改动文件：{exc}"]
    added, removed, changed, kept = [], [], [], []
    for line in names:
        st, _, path = line.partition("\t")
        if not re.search(r"\.(py|ts|tsx|vue)$", path):
            continue
        old = symbols(path, show(repo, base, path)) if st != "A" else {}
        new = symbols(path, show(repo, head, path)) if st != "D" else {}
        for n in new:
            (changed if n in old and old[n] != new[n] else added if n not in old else kept).append((path, n))
        removed += [(path, n) for n in old if n not in new]
    changed_set = set(added) | set(changed)
    out.append(f"增 {len(added)}、删 {len(removed)}、改 {len(changed)} 个公开符号（Python 函数/类/常量，TS export）。")
    sym_lines = []
    for label, items in (("新增", added), ("改动", changed), ("删除", removed)):
        for path, n in items[:MAX_SYMBOLS]:
            rev = base if label == "删除" else head
            hits = refs(repo, rev, n)
            others = [h for h in hits if not h.startswith(path + ":")]
            patches = [h for h in hits if PATCHERS.search(h)]
            if len(hits) > COMMON_HITS:
                sym_lines.append(f"- {label} `{n}`（{path}）：名字太常见（{len(hits)} 处命中），不列位置和打桩点。")
                continue
            row = f"- {label} `{n}`（{path}）：{rev[:8]} 上全仓 {len(hits)} 处命中，文件外 {len(others)} 处"
            if others:
                row += "：" + "; ".join(h.split(":", 2)[0] + ":" + h.split(":", 2)[1] for h in others[:MAX_LOCS])
                row += " …" if len(others) > MAX_LOCS else ""
            if patches:
                row += "。**打桩点**：" + "; ".join(h[:120] for h in patches[:MAX_LOCS])
            sym_lines.append(row)
    out += sym_lines or ["没有公开符号变化。"]
    # Orphans: a name that had a user at base and now has only its own definition. Candidates
    # are the public names of touched files and every identifier on a deleted line, so a
    # definition in an untouched file whose last user the diff removed is found too.
    orphans = []
    added_set = set(added)
    cands = [(path, n) for path, n in kept + list(changed_set) if (path, n) not in added_set][:MAX_SYMBOLS]
    cands += [(None, n) for n in removed_identifiers(repo, base, head)][:MAX_REMOVED]
    names = list(dict.fromkeys(n for _, n in cands))
    now_counts = count_refs(repo, head, names)
    single = {n for n in names if now_counts.get(n, 0) <= 1}
    before_counts = count_refs(repo, base, [n for n in names if n in single])
    done = set()
    for path, n in cands:
        if n in done or n not in single or before_counts.get(n, 0) <= 1:
            continue
        done.add(n)
        now = refs(repo, head, n)
        if len(now) != 1 or (path is None and not DEFINITION.search(now[0].split(":", 2)[-1])):
            continue
        orphans.append((path or now[0].split(":", 1)[0], n, len(refs(repo, base, n)), now[0][:120]))
    out.append("")
    out.append("### 孤立符号（本次改动后只剩定义本身一处引用，改动前不止一处）")
    out += ([f"- ⚠ `{n}`（{p}）：改动前 {b} 处，现在只剩 `{where}`" for p, n, b, where in orphans]
            or ["无。"])
    return out


# ---------------------------------------------------------- 3. trial merge
def added_test_files(repo, base, head):
    files, guard = [], []
    for line in git(repo, "diff", "--name-status", "--no-renames", base, head).splitlines():
        st, _, path = line.partition("\t")
        if st in ("A", "M") and TEST_PATH.search(path):
            files.append((st, path))
    for st, path in files:
        if st != "A":
            continue
        src = show(repo, head, path) or ""
        if GUARD_WORDS.search(src):
            guard.append(path)
    return files, guard


def run_tests(wt, files, test_cmd):
    py = [f for f in files if f.endswith(".py")]
    if not py:
        return None, "新增/改动的测试里没有 Python 文件，这里不跑（前端测试交 CI）"
    if test_cmd:
        cmd = shlex.split(test_cmd.replace("{files}", " ".join(shlex.quote(f) for f in py)))
        cwd = wt
    else:
        cwd = wt
        if all(f.startswith("backend/") for f in py):
            cwd = wt / "backend"
            py = [f[len("backend/"):] for f in py]
        venv_py = wt / "backend" / ".venv" / "bin" / "python"
        cmd = [str(venv_py) if venv_py.exists() else sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *py]
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return None, "测试超过 600 秒，已停"
    except OSError as exc:
        return None, f"测试命令起不来：{exc}"
    text = p.stdout + p.stderr
    tail = "\n".join(text.strip().splitlines()[-12:])
    ran_tests = re.search(r"\d+ (?:passed|failed|error)|FAILED|Traceback|AssertionError", text)
    if p.returncode and not ran_tests and re.search(r"No module named pytest|command not found|Failed to spawn", text):
        return None, "跑测试的环境缺 pytest，没跑成（不是测试失败）：" + tail.splitlines()[-1]
    return p.returncode, tail


def trial(repo, main_ref, merge_refs, head, test_files, test_cmd):
    """Merge main + merge_refs + head in a scratch tree; (status, detail)."""
    tmp = tempfile.mkdtemp(prefix="precompute-")
    wt = Path(tmp) / "t"
    env = {**os.environ, "GIT_AUTHOR_NAME": "p", "GIT_AUTHOR_EMAIL": "p@x", "GIT_COMMITTER_NAME": "p",
           "GIT_COMMITTER_EMAIL": "p@x"}
    try:
        git(repo, "worktree", "add", "-q", "--detach", str(wt), main_ref)
        for name, ref in [*merge_refs, ("本 PR", head)]:
            p = subprocess.run(["git", "-C", str(wt), "merge", "--no-edit", "--no-ff", "-q", ref],
                               capture_output=True, text=True, env=env)
            if p.returncode:
                files = git(wt, "diff", "--name-only", "--diff-filter=U", check=False).split()
                subprocess.run(["git", "-C", str(wt), "merge", "--abort"], capture_output=True)
                return "冲突", f"合入 {name} 时冲突：{', '.join(files) or p.stderr.strip()[:200]}"
        for rel in ("backend/.venv", "frontend/node_modules", "node_modules", ".venv"):
            src = Path(repo) / rel
            if src.exists() and not (wt / rel).exists():
                (wt / rel).symlink_to(src)
        code, tail = run_tests(wt, test_files, test_cmd)
        if code is None:
            return "未跑", tail
        return ("通过" if code == 0 else "失败"), tail
    except (RuntimeError, OSError) as exc:
        return "未跑", str(exc)
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
        subprocess.run(["rm", "-rf", tmp])


def section_trial(repo, base, head, pr, body, main_ref, others, test_cmd):
    out = ["## 3. 与 main / 其他 PR 的试合并"]
    try:
        test_files, guards = added_test_files(repo, base, head)
    except RuntimeError as exc:
        return out + [f"算不出新增测试：{exc}"]
    mentioned = [n for n in dict.fromkeys(PR_MENTION.findall(body)) if n != str(pr)][:4]
    merge_refs = list(others)
    for n in mentioned:
        ref = f"refs/precompute/pr-{n}"
        try:
            p = subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin", f"+refs/pull/{n}/head:{ref}"],
                               capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            out.append(f"- #{n}：取它的 head 超过 60 秒，没试合")
            continue
        if p.returncode == 0:
            merge_refs.append((f"#{n}", ref))
        else:
            out.append(f"- #{n}：取不到它的 head，没试合（{p.stderr.strip()[:100]}）")
    if not guards and not mentioned and not others:
        return out + ["本 PR 没有新增全仓基线守卫测试（`_KNOWN`/`_BASELINE`），正文也没点名别的 PR，不做。"]
    if guards:
        out.append("新增守卫测试：" + ", ".join(f"`{g}`" for g in guards))
    files = [p for _, p in test_files]
    out.append("试合并时跑的测试：" + (", ".join(f"`{f}`" for f in files) or "无"))
    scenarios = [("最新 main + 本 PR", [])] + [(f"最新 main + {n} + 本 PR", [(n, r)]) for n, r in merge_refs]
    for label, mrefs in scenarios:
        status, detail = trial(repo, main_ref, mrefs, head, files, test_cmd)
        out.append(f"- {label}：**{status}**")
        if status != "通过" and detail:
            out.append("  ```\n  " + detail.replace("\n", "\n  ") + "\n  ```")
    for n in mentioned:
        subprocess.run(["git", "-C", str(repo), "update-ref", "-d", f"refs/precompute/pr-{n}"], capture_output=True)
    return out


# ------------------------------------------------------- 4. helper scripts
def run_helper(cmd, cwd, timeout):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"没跑成：{exc}"
    text = (p.stdout + p.stderr).strip()
    return p.returncode, "\n".join(text.splitlines()[-25:])


def section_helpers(tree, repo, base, head, pr_body, scripts, skip_prove, skip_claims):
    out = ["## 4. 回归证明与正文数字复算"]
    scripts = Path(scripts)
    for rel in ("backend/.venv", "frontend/node_modules", "node_modules", ".venv"):
        src, dst = Path(repo) / rel, Path(tree) / rel
        if src.exists() and dst.parent.is_dir() and not dst.exists() and not dst.is_symlink():
            dst.symlink_to(src)
    if skip_prove:
        out.append("- prove-regression --auto：本次跳过。")
    else:
        code, text = run_helper([sys.executable, str(scripts / "prove-regression.py"), "--auto"], tree, 900)
        meaning = {0: "已证明（修前红、修后绿）", 1: "没证明", 2: "判不了（没有测试文件或布局不认识）"}
        out.append(f"- prove-regression --auto：退出 {code}，{meaning.get(code, '没跑成')}")
        out.append("  ```\n  " + text.replace("\n", "\n  ") + "\n  ```")
    if skip_claims:
        out.append("- recompute-pr-claims：本次跳过。")
    else:
        code, text = run_helper([sys.executable, str(scripts / "recompute-pr-claims.py"), "--text", pr_body,
                                 "--base", base, "--head", head, "--repo", str(repo)], tree, 120)
        meaning = {0: "正文里认得的数字都对", 1: "**有数字对不上**", 2: "没有可核的数字或证据不全"}
        out.append(f"- recompute-pr-claims：退出 {code}，{meaning.get(code, '没跑成')}")
        out.append("  ```\n  " + text.replace("\n", "\n  ") + "\n  ```")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tree", help="checked-out review tree (default: --repo)")
    ap.add_argument("--base", required=True, help="merge base")
    ap.add_argument("--head", required=True)
    ap.add_argument("--pr", default="0")
    ap.add_argument("--pr-body", default="")
    ap.add_argument("--scripts", default=str(Path(__file__).resolve().parents[1] / "scripts"))
    ap.add_argument("--report", help="ci-fast-report.json (default: the git dir's)")
    ap.add_argument("--main-ref", default="origin/main")
    ap.add_argument("--other", action="append", default=[], metavar="LABEL=REF", help="extra merge (tests)")
    ap.add_argument("--test-cmd", help="command template with {files}")
    ap.add_argument("--no-prove", action="store_true")
    ap.add_argument("--no-claims", action="store_true")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    tree = a.tree or a.repo
    body = Path(a.pr_body).read_text(errors="replace") if a.pr_body and Path(a.pr_body).is_file() else ""
    others = [tuple(o.split("=", 1)) for o in a.other]
    secs = []
    for fn in (lambda: section_ci(a.repo, tree, a.report, a.head),
               lambda: section_symbols(a.repo, a.base, a.head),
               lambda: section_trial(a.repo, a.base, a.head, a.pr, body, a.main_ref, others, a.test_cmd),
               lambda: section_helpers(tree, a.repo, a.base, a.head, a.pr_body, a.scripts, a.no_prove, a.no_claims)):
        try:
            secs.append("\n".join(fn()))
        except Exception as exc:  # a section that breaks must not take the review down
            secs.append(f"（这一节没算出来：{type(exc).__name__}: {exc}）")
    head = ("## Precomputed facts\n\n脚本在 prepare 时算好的事实，不是结论。第 1 节已 pass 的检查不要再让分身重跑；"
            "没跑的和第 2、3 节的警告是靶心。\n")
    Path(a.out).write_text(head + "\n" + "\n\n".join(secs) + "\n")
    print(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
