#!/usr/bin/env python3
"""precompute.py — facts about a PR that a reviewer would otherwise burn subagents on.

pr-review.sh prepare runs this and appends its PRECOMPUTED.md to REVIEW.md.
Two sections, each a thing a script finds faster and more exactly than a model:

  1. Symbols — public Python/TS symbols the diff added, removed or changed, where the
     whole repo references them, where tests patch them, and exports that the deletion
     left with one reference (their own definition): dead code the diff made.
  2. recompute-pr-claims on the PR's body: the numbers it states, recomputed.

Tests do not run here. CI on GitHub runs them, and the seat running the review runs
whatever experiment a finding needs while it writes the conclusion; a subagent only
reads. A prepare step that ran a suite would be doing a third party's job with the
reviewer's clock.

Nothing here decides anything: it prints facts and a section that cannot be computed
says so. Exit 0 whenever PRECOMPUTED.md was written.

    python3 .claude/pr-review/precompute.py --repo R --tree T --base B --head H \\
        --pr N --pr-body pr.md --scripts .claude/scripts --out PRECOMPUTED.md
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

MAX_SYMBOLS = 300
MAX_LOCS = 8
COMMON_HITS = 200
MAX_REMOVED = 600
TS_EXPORT = re.compile(
    r"^export\s+(?:declare\s+)?(?:default\s+)?(?:abstract\s+)?(?:async\s+)?"
    r"(?:type|interface|const|let|function\*?|class|enum)\s+(\w+)", re.M)
PATCHERS = re.compile(r"setattr|monkeypatch|mocker\.|\bpatch(?:\.object)?\(|vi\.(?:mock|spyOn)|jest\.(?:mock|spyOn)")


class ScanFailed(Exception):
    """A reference scan could not run. Its section reports this rather than zeros:
    a zero count is the answer the orphan scan reads as "nothing else references it",
    so a failure that returns zeros turns a real orphan into silence."""


def git(repo, *args, check=True, env=None):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(args[:3])}: {p.stderr.strip()[:300]}")
    return p.stdout


def show(repo, rev, path):
    p = subprocess.run(["git", "-C", str(repo), "show", f"{rev}:{path}"], capture_output=True)
    return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None


# --------------------------------------------------------------- 1. symbols
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
    """Every whole-word hit of `name` at `rev`, each as `path:line:text`.

    Raises `ScanFailed` on a failed scan, like `count_refs`: an empty list is the
    answer the orphan scan reads as "this name has no other user", so a failure
    that returned one would report the orphan as not-an-orphan. This is the
    function the orphan confirmation and the per-symbol rows read, so the check
    has to be here too, not only in `count_refs`."""
    p = subprocess.run(["git", "-C", str(repo), "grep", "-n", "-I", "-w", "-F", "-e", name, rev],
                       capture_output=True, text=True)
    # git grep exits 1 when there is no match; that is a real empty answer.
    if p.returncode not in (0, 1):
        raise ScanFailed(f"git grep exited {p.returncode} at {rev}: {p.stderr.strip()[:200]}")
    pre = rev + ":"
    return [(l[len(pre):] if l.startswith(pre) else l) for l in p.stdout.splitlines()]


DEFINITION = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|const|let|type|interface|enum)\s|^\s*[A-Za-z_]\w*\s*(?::[^=]+)?=")


#: git's multi-pattern matcher is quadratic in the number of patterns: one grep with 313
#: of them took 341 s on #4070, and 40 took 12.6 s. Ten at a time costs ~1.5 s each, so
#: the same list in batches is seconds. ripgrep does it with one Aho-Corasick scan, and
#: is preferred when the caller can give us a checked-out tree to scan.
REF_CHUNK = 10


def _match_counts(text, names):
    counts = dict.fromkeys(names, 0)
    for w in text.split():
        if w in counts:
            counts[w] += 1
    return counts


def count_refs(repo, rev, names, tree=None):
    """Whole-word matches per name at `rev` (`-o` prints every hit on a line of its own).

    With `tree` (a checked-out revision the caller vouches equals `rev`) ripgrep does
    the scan in one pass. Otherwise the names go to `git grep` in batches of REF_CHUNK,
    because git's own matcher slows down with the size of the pattern list.

    Raises `ScanFailed` when a scan fails instead of returning zeros. A zero count is
    the answer the orphan scan reads as "this name has no other user", so a failed scan
    that came back as zeros would report every orphan as not-an-orphan — the silence
    this function is the only thing able to break."""
    if not names:
        return {}
    pat = "\n".join(names) + "\n"
    if tree and shutil.which("rg"):
        # `--no-filename` is the whole point: without it rg prints `path:NAME` on
        # every match, `_match_counts` sees no bare name, and every count is 0 — the
        # orphan scan then finds nothing and says so. git grep's `-h` does this for
        # the batch branch below.
        # `--no-messages` would swallow the diagnostic that explains a failure, so it
        # is left off and stderr is carried into the error below.
        p = subprocess.run(
            ["rg", "-o", "--no-filename", "-w", "-F", "-f", "/dev/stdin",
             "--no-ignore", "--hidden", "-g", "!.git", str(tree)],
            input=pat, capture_output=True, text=True,
        )
        # rg exits 0 on matches and 1 on none; 2 is a real failure (unreadable path,
        # bad pattern file). Treating 2 as "no matches" is how a whole section goes
        # silently empty.
        if p.returncode not in (0, 1):
            raise ScanFailed(f"rg exited {p.returncode} over {tree}: {p.stderr.strip()[:200]}")
        return _match_counts(p.stdout, names)
    counts = dict.fromkeys(names, 0)
    for i in range(0, len(names), REF_CHUNK):
        batch = names[i:i + REF_CHUNK]
        p = subprocess.run(
            ["git", "-C", str(repo), "grep", "-o", "-h", "-I", "-w", "-F", "-f", "/dev/stdin", rev],
            input="\n".join(batch) + "\n", capture_output=True, text=True,
        )
        # git grep exits 1 when a batch has no match; that is a real answer, not a
        # failure. Anything else (2 on a bad revision or an unreadable object) is.
        if p.returncode not in (0, 1):
            raise ScanFailed(f"git grep exited {p.returncode} at {rev}: {p.stderr.strip()[:200]}")
        for name, n in _match_counts(p.stdout, batch).items():
            counts[name] += n
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


def section_symbols(repo, base, head, tree=None):
    out = ["## 1. 公开符号与引用"]
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
    # The review tree is a clean checkout of `head` (prepare pins it), so ripgrep can scan
    # it directly; the base side has no tree here and goes through batched `git grep`.
    now_counts = count_refs(repo, head, names, tree=tree)
    # -o counts matches, not lines: a definition line that repeats its name counts twice, so the
    # pre-filter is loose (<= 2) and the line counts below are the exact test. A name with no
    # hit at all is not a candidate either — the loop would reject it, but only after a whole
    # `git grep` of its own. Most identifiers on a deleted line land here, and grepping each
    # one cost minutes on a real PR (#4070: 6 of them).
    single = {n for n in names if 0 < now_counts.get(n, 0) <= 2}
    before_counts = count_refs(repo, base, [n for n in names if n in single])
    done = set()
    for path, n in cands:
        if n in done or n not in single or before_counts.get(n, 0) <= 1:
            continue
        done.add(n)
        now = refs(repo, head, n)
        if len(now) != 1 or (path is None and not DEFINITION.search(now[0].split(":", 2)[-1])):
            continue
        before = refs(repo, base, n)
        if len(before) > 1:
            orphans.append((path or now[0].split(":", 1)[0], n, len(before), now[0][:120]))
    out.append("")
    out.append("### 孤立符号（本次改动后只剩定义本身一处引用，改动前不止一处）")
    out += ([f"- ⚠ `{n}`（{p}）：改动前 {b} 处，现在只剩 `{where}`" for p, n, b, where in orphans]
            or ["无。"])
    return out


# ------------------------------------------------------- 2. helper scripts
def run_helper(cmd, cwd, timeout):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"没跑成：{exc}"
    text = (p.stdout + p.stderr).strip()
    return p.returncode, "\n".join(text.splitlines()[-25:])


def section_claims(tree, repo, base, head, pr_body, scripts, skip_claims):
    out = ["## 2. 正文数字复算"]
    if skip_claims:
        return out + ["- recompute-pr-claims：本次跳过。"]
    scripts = Path(scripts)
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
    ap.add_argument("--pr-body", default="")
    ap.add_argument("--scripts", default=str(Path(__file__).resolve().parents[1] / "scripts"))
    ap.add_argument("--no-claims", action="store_true")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    tree = a.tree or a.repo
    secs = []
    for fn in (lambda: section_symbols(a.repo, a.base, a.head, a.tree),
               lambda: section_claims(tree, a.repo, a.base, a.head, a.pr_body, a.scripts, a.no_claims)):
        try:
            secs.append("\n".join(fn()))
        except Exception as exc:  # a section that breaks must not take the review down
            secs.append(f"（这一节没算出来：{type(exc).__name__}: {exc}）")
    head = ("## Precomputed facts\n\n脚本在 prepare 时算好的事实，不是结论。CI 的结果看 PR 上的检查，"
            "这里不重复；第 1 节的警告是靶心。\n")
    Path(a.out).write_text(head + "\n" + "\n\n".join(secs) + "\n")
    print(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
