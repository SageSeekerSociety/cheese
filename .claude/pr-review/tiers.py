#!/usr/bin/env python3
"""Which tier of independent review a change needs, from the files it touches.

    python3 .claude/pr-review/tiers.py --repo ROOT --base BASE --head HEAD
    python3 .claude/pr-review/tiers.py --repo ROOT --base BASE --head HEAD --letter
    python3 .claude/pr-review/tiers.py --repo ROOT --base BASE --head HEAD --rules [--rev REV]
    python3 .claude/pr-review/tiers.py --self-test

`.claude/scripts/pr-review.sh prepare` runs this, prints the block into REVIEW.md and
keeps the letter for the card's first line (`render.py --tier`). The tiers and their
criteria are 《总调度工作流 v2》 §4.

`--rules` prints instead the rule files the change loads, one path per line, read from
`--rev` (prepare passes the default branch): the `.claude/rules/*.md` whose `paths:`
globs cover a changed file, and the AGENTS.md of each directory above one. prepare reads
each of them there — never from the tree under review — so a change cannot edit the rules
it is judged by, the same reason the method itself comes from the default branch.

A change takes the highest tier any of its files falls in — the table below is ranked,
A first. G is not in that ranking: a change is G only when EVERY file is mechanical, and
a file the script cannot call mechanical is judged by its path. 判不了按 B, because B is
what asks a person to read the change; granting G on a guess would skip the reading.

B has a second topology. A B change small enough for one reader — at most
B_FAST_FILES non-test files, at most B_FAST_LINES changed lines, and no file on a
surface branch CI never runs (contract, remote execution, Windows) or on a repo-wide
ratchet baseline — runs one finder instead of two. The letter stays B: the tier says
what kind of change this is, and the conclusion line, render.py and the sweep read a
letter, so the fast path changes what the review spends and not what the card says.

The A paths are the same vocabulary `.claude/scripts/orchestrator_autoaccept.py` gates
auto-accept on (its HIGH_RISK and PROD_DEPLOY): a change too risky to accept unattended
must not be a lighter tier here either. `--self-test` fails when the two lists drift.
"""

import fnmatch
import re
import subprocess
import sys
from pathlib import Path

#: The ranked table: (letter, name, what it covers, what to review, how deep, topology).
#: The criteria text is handed to the reviewer as-is; the doc's "who reviews" column is not
#: here, because a seat running a lighter tier still decides how many subagents to spend on
#: it — and the topology says how many, so that decision is the same one every time.
#:
#: A topology names stages, not seats: how many finders run over the change, how many
#: refuters may be judging blocker and major candidates at once, and whether the claim
#: axis gets a subagent of its own. The stages run one after another — the finders
#: together, then the refuters, then the claim finder — so the most a tier has running at
#: once is its largest stage and not their sum, which `max_in_flight` returns and the
#: self-test holds under the environment's cap of 4 concurrent subagents. A refuter's
#: count is a concurrency, not a budget: every blocker and major candidate gets judged,
#: by the next refuter started as one returns.
#:
#: A refuter is spent only on a candidate that came back blocker or major, which is why
#: the single-finder tiers still carry one: a change filed under the wrong tier must not
#: also lose the independent verdict its serious findings are entitled to. `effort` is
#: what the coordinator passes a subagent (low / medium / high). Neither here nor in
#: prompt.md does a seat or a model name appear: which ones exist changes between
#: sessions, and a review has to run on whatever the session it runs in has.
TIERS = (
    (
        "A",
        "高风险",
        "迁移、鉴权、部署链、计量代理、用户可见文案",
        "可逆性、鉴权边界、影响面、回滚方式；文案看会不会改变用户已定的状态",
        "按包分组、每组一路找茬，只对 blocker/major 逐条证伪；碰正式部署一律人定",
        {"finders": 2, "refuters": 2, "claim": 1, "effort": "high"},
    ),
    (
        "B",
        "实现代码",
        "后端、前端逻辑，API，脚本",
        "做没做对需求、可达路径上的错行为、边界与并发、复杂度；每条问题带 文件:行",
        "按包分组、每组一路全新上下文找茬，各组结论汇合后逐条证伪只留站得住的",
        {"finders": 2, "refuters": 2, "claim": 0, "effort": "high"},
    ),
    (
        "C",
        "测试",
        "只改测试、夹具、守卫自测",
        "测试会不会咬人：一次变异必须红；断言两边是否同源",
        "按包分组；重点是测试咬不咬人，变异由协调者在结论阶段跑",
        {"finders": 1, "refuters": 1, "claim": 0, "effort": "medium"},
    ),
    (
        "D",
        "配置与 CI",
        "workflow、pre-commit、settings、路由表",
        "影响面、密钥有没有写进去、能不能回滚；与代码改动拆开",
        "按包分组，读全 diff",
        {"finders": 1, "refuters": 1, "claim": 0, "effort": "medium"},
    ),
    (
        "E",
        "文档与说明",
        "AGENTS.md、.claude/rules、技能、README",
        "每句问「删掉会不会出错」；有没有已被 lint 或测试拦着的；引用的命令和路径存不存在",
        "按包分组，只读",
        {"finders": 1, "refuters": 1, "claim": 0, "effort": "medium"},
    ),
)
MECHANICAL = (
    "G",
    "机械改动",
    "纯注释、纯格式、lock、生成文件、干净 revert",
    "确认确实是机械的",
    "不审，CI 过即可",
    {"finders": 0, "refuters": 0, "claim": 0, "effort": "low"},
)

#: B 档的第二套拓扑：影响面小到一个人一次能读完的常规改动只派一路找茬。判定见
#: b_fast()：非测试改动文件不超过 B_FAST_FILES 个，改动不超过 B_FAST_LINES 行，且
#: 没有文件落在 wide_surface() 点名的面上。档位字母仍是 B——档位说的是改动的种类，
#: 结论行、render.py 与 sweep 只认字母，快路径改的是这次审查花多少分身，不是卡片写
#: 什么。数字来自 #1936 第一轮回测：14 张 B 档 PR 平均 13.5 分钟、强制打回 0 次、
#: 26 条 minor 只有 3 条被采纳，两路 high effort 在这类改动上没有换回东西。
B_FAST = {"finders": 1, "refuters": 1, "claim": 0, "effort": "high"}
B_FAST_FILES = 6
B_FAST_LINES = 250
#: 快路径「审多深」那一句：B 档的表里写的是按包分组，而这一档的改动小到一组就够。
B_FAST_DEPTH = "按包分组后每组一路全新上下文找茬，跨包由协调者点，再逐条证伪只留站得住的"

#: 让一张小 B 档不能只派一路的面，按路径里的字面词匹配。两件事都在说同一件事：
#: PR 上的 CI 不跑它（remote），或者它跨了前后端或平台（contract、Windows），或者它的数字和别的在途 PR 相撞
#: （全仓棘轮基线）。宁严勿松：误判只是多花一路分身，漏判才是把该两路看的改动交给
#: 一个人。
WIDE_SURFACE = (
    ("contract", "契约层"),
    ("remote", "远程执行，PR 上不跑"),
    ("windows", "Windows 专属代码，分支 CI 不跑"),
    ("ratchet", "全仓棘轮"),
    ("baseline", "全仓棘轮基线"),
)

#: 迁移、鉴权、部署链、用户可见文案，as (what, prefix) — identical to
#: orchestrator_autoaccept.HIGH_RISK, which the self-test checks.
HIGH_RISK = (
    ("迁移", "backend/alembic/"),
    ("鉴权", "backend/app/auth/"),
    ("鉴权", "backend/app/api/auth/"),
    ("鉴权", "backend/app/domain/identity/"),
    ("鉴权", "backend/app/domain/authz/"),
    ("鉴权", "cli/internal/auth/"),
    ("部署链", "deploy/"),
    ("部署链", ".github/workflows/deploy"),
    ("部署链", "scripts/redeploy.sh"),
    ("部署链", ".github/workflows/test.yml"),
    ("部署链", ".github/workflows/build.yml"),
    ("部署链", ".github/workflows/required-ci.yml"),
    ("文案", "frontend/src/i18n/messages/"),
    ("文案", "backend/app/core/sentences.py"),
)
#: The production deploy chain — identical to orchestrator_autoaccept.PROD_DEPLOY.
PROD_DEPLOY = (
    ".github/workflows/deploy-prod.yml",
    ".github/workflows/deploy.yml",
    "deploy/deploy.sh",
    "deploy/deploy-docker.sh",
    "deploy/registry-retry.sh",
    "deploy/docker-compose.prod.yml",
    "deploy/.env.prod.example",
    "deploy/compose/",
)
#: Where the `paths:`-scoped rules live, and the rev `--rules` reads them from when prepare
#: passes no `--rev`: the same revision the method itself is read from, so neither can be
#: edited by the change under review.
RULES_DIR = ".claude/rules/"
DEFAULT_REV = "origin/main"

CONFIG = (
    ".github/",
    ".pre-commit-config.yaml",
    ".claude/settings.json",
    "backend/scripts/route_index.json",
    "docker-compose.yml",
    "docker-compose.dev.yml",
    "Taskfile.yml",
)
DOCS = (".claude/rules/", ".claude/skills/", ".claude/agents/", ".agents/", "docs/")
#: What under `docs/` is prose a person reads: the site's pages, plain text, tabular data and
#: images. Everything else there is machinery a build or a checker reads or runs — the site
#: generator (`.mjs`), its entry script (`.sh`), the npm and task files (`.json`, `.yml`), the
#: diagram sources — so a change to one is read like code. A list of what stays doc rather
#: than of what is code, because the code side is open-ended: the first list
#: (`.js/.mjs/.ts/.py/.css/.vue`) still left the `.sh` that `docs/site/package.json` calls in
#: the doc tier, and §4 stops re-reading that tier after a review. Only the `docs/` prefix is
#: matched: the other DOCS entries hold rule and skill text, where a stray script is still
#: documentation about how to do something.
DOC_PROSE = (".md", ".html", ".txt", ".csv", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf")
LOCKED = (
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "uv.lock",
    "Cargo.lock",
    "bun.lock",
    "poetry.lock",
    "skills-lock.json",
    ".claude/scheduled_tasks.lock",
)
GENERATED = ("frontend/src/api/openapi.d.ts",)
#: Line comments by extension, for "纯注释" — the token a changed line may start with. An
#: extension not here cannot be judged mechanical, and a changed line whose string value
#: holds a comment marker does not start with one, so it is not mechanical either.
#:
#: No "*" token. A line starting with one is a block comment's continuation in JS/TS, but
#: it is a dereference in Go and Rust (`*p = 3`) and the universal selector in CSS, and a
#: diff line does not say which. A block comment's continuation lines are therefore not
#: judged mechanical; the file falls to the tier its path asks for — 判不了按 B.
#: File types whose indentation is syntax: a line's leading whitespace is part of what it
#: means, so changing it is not 纯格式.
INDENTED = frozenset({".py", ".yml", ".yaml"})

COMMENT = {
    ".py": ("#",),
    ".sh": ("#",),
    ".yml": ("#",),
    ".yaml": ("#",),
    ".toml": ("#",),
    ".ini": ("#",),
    ".cfg": ("#",),
    ".ts": ("//", "/*"),
    ".tsx": ("//", "/*"),
    ".js": ("//", "/*"),
    ".jsx": ("//", "/*"),
    ".mjs": ("//", "/*"),
    ".cjs": ("//", "/*"),
    ".scss": ("//", "/*"),
    ".css": ("/*",),
    ".go": ("//", "/*"),
    ".rs": ("//", "/*"),
    ".sql": ("--",),
    ".vue": ("<!--", "//", "/*"),
    ".html": ("<!--",),
    ".md": ("<!--",),
}


def under(path, prefix):
    """True when `path` is `prefix` or under it. A prefix ending in "/" is a directory."""
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path == prefix or (path.startswith(prefix) and "/" not in path[len(prefix) :])


def is_test(path):
    name = path.rsplit("/", 1)[-1]
    # This runs tests; it is not itself a test module.
    claude_script_self_test = (
        path.startswith(".claude/scripts/")
        and name.endswith(".py")
        and (name.startswith("test-") or name.endswith("-test.py"))
    )
    return (
        name.endswith((".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx",
                       ".test.ts", ".test.tsx", ".test.js", ".test.jsx"))
        or (name.startswith("test_") and name.endswith(".py"))
        or name.endswith("_test.py")
        or claude_script_self_test
        or "/tests/" in path
        or "/test/" in path
        or "__tests__/" in path
        or path.startswith("e2e/")
        or "/e2e/" in path
    )


def is_doc(path):
    """Whether this path is read as documentation. A `.md` always is. Under `docs/`, only the
    formats a person reads are (DOC_PROSE): judging by the prefix alone put the site
    generator and its entry scripts in the doc tier, which §4 also stops re-reading after a
    review — the same hole as the copy prefix in ready.py, reached through a different
    door. Naming the code extensions instead does not close it: the first such list missed
    the `.sh` that runs the site's diagram build, which is why the list here is the one of
    what stays doc."""
    if path.endswith(".md"):
        return True
    if not any(under(path, p) for p in DOCS):
        return False
    if under(path, "docs/"):
        return path.endswith(DOC_PROSE)
    return True


def tier_of_path(path):
    """"A"–"E" for one file, by name alone: the first rule that matches wins, and B is
    what is left, so a path nobody wrote a rule for is reviewed rather than waved past."""
    if any(under(path, p) for _, p in HIGH_RISK) or any(under(path, p) for p in PROD_DEPLOY):
        return "A"
    if is_test(path):
        return "C"
    if any(under(path, p) for p in CONFIG):
        return "D"
    if is_doc(path):
        return "E"
    return "B"


def _git(repo, *args):
    """git, with path quoting off. `core.quotePath` is on by default, so any path holding a
    non-ASCII byte comes back as `"\\346\\226\\260.py"` — not a path, matches no prefix and
    resolves to no blob. Callers that read paths off git output use paths_of() below, which
    is stronger still; this covers the rest."""
    out = subprocess.run(
        ["git", "-c", "core.quotePath=false", "-C", str(repo), *args],
        capture_output=True, text=True,
    )
    return out.stdout if out.returncode == 0 else None


def paths_of(repo, *args):
    """`git diff --name-only -z <args>` as a list of paths, or None when git failed. `-z`
    rather than line splitting: it turns quoting off entirely, so a path holding a quote or
    a newline arrives whole. A path that arrives quoted resolves to no blob, and the
    comparison that decides whether a file was touched then reads it as untouched."""
    out = _git(repo, "diff", "--name-only", "-z", *args)
    return None if out is None else [p for p in out.split("\0") if p]


def changed_lines(repo, base, head, paths):
    """Added plus removed lines over `paths`, or None when git cannot count them. `-z` for
    the same reason paths_of uses it: a path holding a quote or a newline arrives whole.
    A binary file reports "-" for both counts, which is not a line count; the caller reads
    None as 判不了 and keeps the heavier topology, the same way 判不了按 B does."""
    out = _git(repo, "diff", "--numstat", "-z", "--no-renames", base, head, "--", *paths)
    if out is None:
        return None
    total = 0
    for field in out.split("\0"):
        if not field:
            continue
        cols = field.split("\t")
        if len(cols) < 3 or not cols[0].isdigit() or not cols[1].isdigit():
            return None
        total += int(cols[0]) + int(cols[1])
    return total


def wide_surface(path):
    """Why this path keeps a B change on the two-finder topology, or "" when it is an
    ordinary one. Matched on the lower-cased whole path, so a Windows-only file is caught
    whichever directory it sits in: `_windows.go` is not covered by branch CI, and neither
    is anything else that names a platform this repo only runs remotely."""
    low = path.lower()
    for token, why in WIDE_SURFACE:
        if token in low:
            return why
    return ""


def b_fast(repo, base, head, hard):
    """True when this B change runs the single-finder topology. `hard` is the files that
    need reading, as classify() already worked them out; a mechanical lock file does not
    count toward the size, because nobody reads it.

    Three conditions, in the words they are checked with: at most B_FAST_FILES changed
    files that are not tests, at most B_FAST_LINES changed lines across the files that
    need reading, and no file on a wide surface. A change git cannot measure is not fast:
    not being able to count is not the same as being small."""
    read = [p for p in hard if not is_test(p)]
    if not read or len(read) > B_FAST_FILES:
        return False
    if any(wide_surface(p) for p in hard):
        return False
    lines = changed_lines(repo, base, head, hard)
    return lines is not None and lines <= B_FAST_LINES


def mechanical(repo, base, head, path):
    """True when this file's change is one of §4's mechanical kinds: a lock or generated
    file, a diff of comments, whitespace and blank lines only, or a clean revert to a
    version the file already had. False also means "could not tell", which is the answer
    判不了按 B needs.

    A high-risk path is never mechanical, however small the edit: a one-line change to a
    migration or to an auth check is still a change to a migration or an auth check."""
    if tier_of_path(path) == "A":
        return False
    if path.rsplit("/", 1)[-1] in LOCKED or path in GENERATED or path.endswith(".lock"):
        return True
    if liftable_only(repo, base, head, path) or clean_revert(repo, base, head, path):
        return True
    return False


def liftable_only(repo, base, head, path):
    """Every added AND every removed line is blank, starts with this file's comment token,
    or is paired with a line on the other side that differs from it only in spacing —
    comments and whitespace-only edits, which is what §4's 纯注释 and 纯格式 come to in a
    diff.

    The file is compared whole, comments and blank lines left out: its code lines at
    `head`, read top to bottom, must be its code lines at `base` with only their spacing
    changed. A line added or deleted breaks that, and deleting a line of code is not
    mechanical: it is how a removed guard or a removed feature still gets read. Moving a
    line of code breaks it too — it changes the order things run in. In a file whose
    indentation is syntax (INDENTED), a line's leading whitespace must stay as it was: in
    Python moving a `return` out of an `if` changes what runs, and in YAML re-indenting a
    key moves it to another mapping. A file type with no entry in COMMENT is not judged
    here (False), not assumed clean."""
    suffix = Path(path).suffix
    tokens = COMMENT.get(suffix)
    if not tokens:
        return False
    before = _git(repo, "show", f"{base}:{path}")
    after = _git(repo, "show", f"{head}:{path}")
    if before == after:
        return False

    def code(text):
        out = []
        for raw in (text or "").splitlines():
            body = raw.strip()
            if not body or body.startswith(tokens):
                continue
            indent = raw[: len(raw) - len(raw.lstrip())] if suffix in INDENTED else ""
            out.append(indent + "".join(body.split()))
        return out

    return code(before) == code(after)


def clean_revert(repo, base, head, path):
    """`head`'s version of `path` is a version the file already had before `base`: the
    change puts a known old content back, so there is nothing new to read. `--find-object`
    names the commit that moved the file off that blob; any hit means it was in the file."""
    blob = _git(repo, "rev-parse", "--verify", "--quiet", f"{head}:{path}")
    if not blob:
        return False
    found = _git(repo, "log", "--format=%H", "-n", "1", f"--find-object={blob.strip()}",
                 base, "--", path)
    return bool(found and found.strip())


def _exists(repo, rev, path):
    """Whether `rev` holds `path`. `_git` cannot answer this: `git show` of an empty file
    and of a missing one both come back as empty output."""
    return subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{rev}:{path}"], capture_output=True,
    ).returncode == 0


def paths_frontmatter(text):
    """The globs in a rule file's `paths:` frontmatter. Both spellings are read — the block
    one (`paths:` then one `- "glob"` per line) and the flow one (`paths: ["a", "b"]`), as
    the rule files in this repo use the block one. An empty list means the file declares
    none, which `rule_files` reads as applying to every change, not to none: a rule nobody
    scoped is the one a reviewer is likeliest to miss."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        return []
    globs, collecting = [], False
    for line in lines[1:end]:
        # The list item is read before the key, because a block sequence may sit at column 0
        # — `paths:` with the globs unindented is the same YAML as the indented spelling.
        # Read as a key it is not `paths`, the globs are dropped, and an empty list makes
        # the rule look unscoped, which injects it into every review there is.
        if collecting and line.strip().startswith("-"):
            globs.append(line.strip()[1:].strip().strip("'\""))
            continue
        if line.strip() and not line[:1].isspace():
            collecting = False
            key, _, rest = line.partition(":")
            if key.strip() != "paths":
                continue
            rest = rest.strip()
            if rest.startswith("["):
                inner = rest[1:].rsplit("]", 1)[0]
                return [g.strip().strip("'\"") for g in inner.split(",") if g.strip()]
            collecting = True
    return globs


def rule_glob_match(path, glob):
    """Whether a rule's `paths:` glob covers a changed file, with fnmatch — permissive on
    purpose: fnmatch's `*` crosses "/", so `backend/app/**` and `backend/app/**/*.py` both
    cover `backend/app/deep/x.py` and neither misses a file the author meant. Injecting a
    rule that turns out not to apply costs the reviewer a few lines; missing one loses the
    rule the change was written under, which is the whole point of injecting them."""
    return fnmatch.fnmatchcase(path, glob)


def rule_files(repo, rev, changed):
    """The rule files a change loads, as repo-relative paths: each `.claude/rules/*.md` at
    `rev` whose `paths:` globs cover a changed file, then the AGENTS.md of each directory
    above one.

    Read from `rev` — prepare passes the default branch — never from the tree under review,
    so a change cannot rewrite the rules that judge it. A rev that lists no rule directory
    (a shallow clone, a branch that does not exist) yields none rather than failing.

    The repo's own AGENTS.md is left out: it is the root one, and the reviewer's session
    already has it. The per-directory ones are what a reviewer reading one diff would
    otherwise never open."""
    listed = _git(repo, "ls-tree", "-r", "--name-only", rev, "--", RULES_DIR) or ""
    rules = []
    for name in sorted(n for n in listed.splitlines() if n.endswith(".md")):
        text = _git(repo, "show", f"{rev}:{name}")
        if text is None:
            continue
        globs = paths_frontmatter(text)
        if globs and not any(rule_glob_match(p, g) for p in changed for g in globs):
            continue
        rules.append(name)

    dirs = []
    for path in changed:
        parts = path.split("/")[:-1]
        for depth in range(len(parts)):
            parent = "/".join(parts[:depth + 1])
            if parent and parent not in dirs:
                dirs.append(parent)
    agents = [f"{d}/AGENTS.md" for d in sorted(dirs, key=lambda d: (d.count("/"), d))
              if _exists(repo, rev, f"{d}/AGENTS.md")]
    return agents + rules


def classify(repo, base, head):
    """(letter, detail, topology) for the change `base`..`head` — the whole PR, so a delta
    review still reports the tier of what is being merged.

    The topology is the tier's own, except for a B change b_fast() calls small: that one
    spends the single-finder topology while its letter stays B, because the letter is what
    says what kind of change this is and three other programs read it back."""
    paths = paths_of(repo, "--no-renames", base, head)
    if paths is None:
        return "B", "读不到改动的文件，按 B 档", topology("B")
    if not paths:
        return "G", "没有改到任何文件", topology("G")
    hard = [p for p in paths if not mechanical(repo, base, head, p)]
    if not hard:
        why = "、".join(f"{p}（{_mechanical_why(p)}）" for p in sorted(paths)[:6])
        return "G", why, topology("G")
    rank = {t[0]: i for i, t in enumerate(TIERS)}
    letter = min((tier_of_path(p) for p in hard), key=lambda t: rank[t])
    drivers = sorted(p for p in hard if tier_of_path(p) == letter)
    detail = "、".join(f"{p}（{_why(p, letter)}）" for p in drivers[:4])
    if letter == "B" and b_fast(repo, base, head, hard):
        return letter, detail, B_FAST
    return letter, detail, topology(letter)


def _why(path, letter):
    if letter == "A":
        hit = next((what for what, prefix in HIGH_RISK if under(path, prefix)), None)
        return hit or ("正式部署" if any(under(path, p) for p in PROD_DEPLOY) else "高风险")
    return {"B": "实现代码", "C": "测试", "D": "配置与 CI", "E": "文档与说明"}.get(letter, "")


def _mechanical_why(path):
    if path in GENERATED:
        return "生成文件"
    if path.endswith(".lock") or path.rsplit("/", 1)[-1] in LOCKED:
        return "lock 文件"
    return "只动注释、空格或缩进，或是干净 revert"


def row(letter):
    return MECHANICAL if letter == "G" else next(t for t in TIERS if t[0] == letter)


def topology(letter):
    """The subagent topology of a tier: `finders`, `refuters`, `claim` and `effort`."""
    return row(letter)[5]


def max_in_flight(top):
    """How many subagents one tier may have running at once. The stages run in order —
    the finders together, then the refuters together, then the claim finder — so this is
    the largest stage, not their sum, which is what the environment's cap of 4 concurrent
    subagents counts."""
    return max(top["finders"], top["refuters"], top["claim"])


def topology_line(top):
    """One topology as a line of Chinese, for the `- 子代理拓扑：` bullet in REVIEW.md.
    It takes the topology itself, not a letter: a B change on the fast path spends a
    topology its letter does not name, and the line has to say what this review spends.
    It names counts and effort, never a seat or a model: which of those a session has
    changes between sessions, and a review must run on whatever it was handed."""
    if not top["finders"]:
        return "不跑 OCR"
    # The finding is one open-code-review run: it groups the change by package and
    # gives each group its own loop, so the counts below are the concurrency inside
    # that run, not subagents this seat starts. Refutation is no longer a stage —
    # the coordinator runs the commands that settle a candidate (method step 4).
    parts = [f"跑一次 OCR，它按包分组、每组一路主审查（最多 {top['finders']} 路并行）"]
    if top["claim"]:
        parts.append(f"{top['claim']} 路单查需求契约")
    # 「本档的判读投入」, not 「审查 effort」: OCR's own --effort is a different knob
    # (rounds per file) and the method pins it at low, so a seat that read this as
    # "pass effort=<this> to OCR" would triple every file's rounds.
    return (f"单轮最多 {max_in_flight(top)} 路并发："
            + "，".join(parts) + f"；本档判读投入 {top['effort']}（OCR 的 --effort 固定 low）")


def block(letter, detail, top=None):
    """The markdown `prepare` puts in REVIEW.md: the tier, why this change is in it, the
    criteria of that tier as the doc words them, and the subagent topology to run it.

    `top` is the topology classify() decided, which is the tier's own except on the B fast
    path; the depth bullet follows it, so the section cannot say two finders above its own
    子代理拓扑 line saying one."""
    _, name, covers, criteria, depth, _ = row(letter)
    top = top or topology(letter)
    if letter == "B" and top == B_FAST:
        depth = B_FAST_DEPTH
    head = f"**档位 {letter} {name}**（{covers}）"
    if letter == "G":
        head += "——免审，CI 过即可"
    return "\n".join((head, f"- 为什么会是这一档：{detail}",
                      f"- 审什么：{criteria}", f"- 审多深：{depth}",
                      f"- 子代理拓扑：{topology_line(top)}"))


def self_test():
    """Offline: a throwaway repository, no network, no gh. Each case is one way the tier
    could be wrong — the A table drifting from the auto-accept script's, a path rule
    matching the wrong file, a mechanical change judged as code, or a real code change
    waved through as mechanical."""
    import tempfile

    bad, total = [], 0

    def check(name, got, want):
        nonlocal total
        total += 1
        if got != want:
            bad.append(f"{name}\n  想要 {want!r}\n  读成 {got!r}")

    for path, want in (
        ("backend/alembic/versions/a.py", "A"),
        ("backend/app/auth/caller.py", "A"),
        ("deploy/metering-proxy/billing_addon.py", "A"),
        ("frontend/src/i18n/messages/en/ask.json", "A"),
        ("deploy/deploy-prod.yml", "A"),
        ("backend/app/domain/review/pr_text.py", "B"),
        ("backend/tests/unit/test_pr_text_trailers.py", "C"),
        ("backend/test_x.py", "C"),  # test_ prefix
        ("backend/scripts/fusion_boot_test.py", "C"),  # _test.py suffix
        (".claude/scripts/test-review.py", "C"),  # test- self-test
        (".claude/scripts/review-test.py", "C"),  # -test.py self-test
        (".claude/scripts/review.py", "B"),  # ordinary implementation script
        (".claude/scripts/testing.py", "B"),  # not test- or -test.py
        (".claude/scripts/latest.py", "B"),  # no hyphen before test.py
        (".claude/scripts/test-dev-db.sh", "B"),  # only Python test files match
        (".claude/test-review.py", "B"),  # hyphen pattern is scoped to scripts/
        ("scripts/test-review.py", "B"),  # hyphen pattern is scoped to .claude/scripts/
        ("backend/tests/conftest.py", "C"),  # /tests/ alone, no test name of its own
        ("docs/site/test/ask-walk.test.mjs", "C"),  # /test/ alone: not a .test.js suffix
        ("frontend/src/components/__tests__/acceptHarness.ts", "C"),  # __tests__/
        ("e2e/AGENTS.md", "C"),  # e2e/ prefix: a test path beats the .md rule below
        ("cli/e2e/task.go", "C"),  # /e2e/
        ("frontend/src/views/Home.spec.ts", "C"),
        ("e2e/tests/task.spec.ts", "C"),
        (".github/workflows/cli.yml", "D"),
        ("backend/scripts/route_index.json", "D"),
        ("AGENTS.md", "E"),
        (".claude/rules/migrations.md", "E"),
        ("docs/accept-is-merge.md", "E"),
        ("deploy/README-backup.md", "A"),  # deploy/ wins over .md: A is tested first
        # A prefix is not enough: the copy and doc prefixes hold code too, and a file
        # judged by its prefix alone lands in a tier that is also an exemption (§4 stops
        # re-reading it after a review) while CI keeps reading it as code. The loaders beside the message files stay in A, where the copy
        # prefix already put them: heavier is the safe direction. What was wrong is the
        # increment exemption, and that lives in ready.exempt()'s self-test.
        ("frontend/src/i18n/messages/catalog.ts", "A"),  # the loader, not a message file
        ("frontend/src/i18n/messages/zh-CN/index.ts", "A"),
        ("frontend/src/i18n/messages/zh-CN/sentences.ts", "A"),
        ("docs/site/build.mjs", "B"),
        ("docs/site/src/arch.mjs", "B"),
        ("docs/site/gen/prompt.py", "B"),
        ("docs/evidence/preview-copy/rows-for-pdf.mjs", "B"),
        # The code side of `docs/` is open-ended, so a list of code extensions leaves the
        # next one out: `.sh` and `.yml` were missing, and `docs/site/package.json` runs
        # `bash gen/diagrams.sh`. The rule is a list of prose formats instead, and every
        # extension nobody listed — including one this table does not name — is code.
        ("docs/site/gen/diagrams.sh", "B"),
        ("docs/site/Taskfile.yml", "B"),
        ("docs/site/package.json", "B"),
        ("docs/site/diagrams/turn.sequence.json", "B"),  # read by the generator
        ("docs/evidence/preview-copy/manifest.json", "B"),
        ("frontend/src/i18n/messages/zh-CN/ask.json", "A"),  # a message file still is copy
        ("docs/site/diagrams/overview.html", "E"),  # generated page, still prose
        ("docs/evidence/ci-rounds-followup-2026-10-14.csv", "E"),  # tabular, a person reads it
        (".claude/rules/frontend.md", "E"),
        # Only `docs/` gets the prose list. The same extension under a rule or skill tree is
        # documentation about how to do something, not code the repo runs.
        (".claude/skills/cheese/scripts/setup.sh", "E"),
    ):
        check(f"按路径定档：{path}", tier_of_path(path), want)

    # The topology is what the tier actually spends, so it is checked as a table rather
    # than through the sentence block() prints: a stage count that drifted would otherwise
    # show up only as a review that quietly costs more, or reads less, than it did before.
    check("A–E 每一档都带子代理拓扑", [len(t) for t in TIERS], [6] * 5)
    check("G 档也带子代理拓扑", len(MECHANICAL), 6)
    check("每档拓扑的字段名一样", [sorted(topology(l)) for l in "ABCDEG"],
          [["claim", "effort", "finders", "refuters"]] * 6)
    check("每档的子代理 effort 只有三档之一", [topology(l)["effort"] for l in "ABCDEG"],
          ["high", "high", "medium", "medium", "medium", "low"])
    check("单轮并发数不超过环境上限 4", [max_in_flight(topology(l)) for l in "ABCDEG"],
          [2, 2, 1, 1, 1, 0])
    check("G 档不派任何分身", topology("G"),
          {"finders": 0, "refuters": 0, "claim": 0, "effort": "low"})
    # Refutation is gone (method step 4 runs the commands), so the refuter counts stay
    # in the table only because the tier table is not this change's to reshape; what
    # the line must still say is that the finding is one OCR run and at what effort.
    check("每一档的块里都写了拓扑", [("子代理拓扑" in block(l, "x")) for l in "ABCDEG"],
          [True] * 6)
    # What the line has to say, not the sentence saying it: the numbers are the tier's own
    # topology numbers, so a rewording that keeps the rules keeps the test green, and a
    # count that drifts does not.
    check("拓扑行里的数字就是这一档拓扑的数字",
          [(sorted({int(n) for n in re.findall(r"\d+", topology_line(topology(l)))}),
            len(re.findall(r"\d+", topology_line(topology(l))))) for l in "ABCDE"],
          [([1, 2], 3), ([2], 2), ([1], 2), ([1], 2), ([1], 2)])
    check("A–E 的拓扑行都说跑一次 OCR",
          [("跑一次 OCR" in topology_line(topology(l))) for l in "ABCDE"], [True] * 5)
    check("拓扑行报出这一档的 effort",
          [topology(l)["effort"] in topology_line(topology(l)) for l in "ABCDE"], [True] * 5)
    check("G 档的拓扑行说不跑 OCR", "- 子代理拓扑：不跑 OCR" in block("G", "x"), True)

    # B 档的快路径：它换的是拓扑，不换档位字母。字母是结论行、render.py 与 sweep 读回去的
    # 那个字段，改成 B-fast 就会让三处都认不出来；而拓扑写进 REVIEW.md，是 reviewer 真正
    # 照着跑的东西，所以快路径必须在那行里看得见。
    check("B 快路径只派一路找茬", B_FAST,
          {"finders": 1, "refuters": 1, "claim": 0, "effort": "high"})
    check("B 快路径仍留着一路独立证伪", B_FAST["refuters"], 1)
    check("B 快路径的单轮并发只有 1", max_in_flight(B_FAST), 1)
    check("B 快路径的拓扑行说单轮一路并发", "1 路并发" in topology_line(B_FAST), True)
    check("B 快路径的拓扑行说按包分组", "按包分组" in topology_line(B_FAST), True)
    check("B 快路径的块里那行拓扑就是快路径",
          f"- 子代理拓扑：{topology_line(B_FAST)}" in block("B", "x", B_FAST), True)
    check("B 快路径的块仍写档位 B",
          block("B", "x", B_FAST).splitlines()[0].startswith("**档位 B "), True)
    check("B 快路径的「审多深」跟着拓扑，不说两路",
          "两路" in block("B", "x", B_FAST), False)
    check("没有快路径时 B 档单轮两路并发", topology_line(topology("B")).count("2 路并发"), 1)

    check("块状 paths 前言读出来", paths_frontmatter('---\npaths:\n  - "backend/tests/**"\n---\n\n# 标题\n'),
          ["backend/tests/**"])
    check("行内 paths 前言读出来", paths_frontmatter('---\npaths: ["a/**", "b.ts"]\n---\n'),
          ["a/**", "b.ts"])
    check("顶格的块状 paths 前言也读出来",
          paths_frontmatter('---\npaths:\n- "backend/tests/**"\n- "b.ts"\n---\n'),
          ["backend/tests/**", "b.ts"])
    check("没有前言的规则文件算没写 paths", paths_frontmatter("# 规矩\n"), [])
    check("前言里没有 paths 也算没写", paths_frontmatter("---\ntype: rule\n---\n"), [])
    check("paths 列表遇到下一个前言键就收住",
          paths_frontmatter('---\npaths:\n  - "a/**"\ntype: rule\n  - "b/**"\n---\n'), ["a/**"])
    check("fnmatch 的 * 会跨 /，深一层的文件也命中",
          rule_glob_match("backend/app/domain/x.py", "backend/app/**"), True)
    check("glob 外的文件不命中", rule_glob_match("frontend/src/a.ts", "backend/app/**"), False)

    with tempfile.TemporaryDirectory(prefix="tiers-test-") as tmp:
        repo = Path(tmp)
        for args in (("init", "-q", "-b", "main", str(repo)),):
            subprocess.run(["git", *args], check=True, capture_output=True)
        for k, v in (("user.email", "t@example.com"), ("user.name", "t"),
                     ("commit.gpgsign", "false")):
            subprocess.run(["git", "-C", str(repo), "config", k, v], check=True)
        write = lambda rel, text: (repo / rel).parent.mkdir(parents=True, exist_ok=True) or (
            repo / rel).write_text(text)

        write("f.py", "x = 1\n")
        write("frontend/pnpm-lock.yaml", "lockfileVersion: 1\n")
        write("frontend/src/api/openapi.d.ts", "export type A = 1;\n")
        write("backend/app/auth/caller.py", "ok = True\n")
        write("backend/alembic/versions/v.py", "revision = 'v'\n")
        # What `--rules` reads: the repo's own AGENTS.md files and paths-scoped rules. They
        # sit in the base commit so the cases below can ask for them at a rev the change
        # under review never touched.
        write("AGENTS.md", "仓库规矩\n")
        write("backend/AGENTS.md", "后端规矩\n")
        write(".claude/rules/always.md", "# 没有 paths 的规矩\n")
        write(".claude/rules/backend-tests.md",
              '---\npaths:\n  - "backend/tests/**"\n---\n\n# 后端测试\n')
        write(".claude/rules/frontend.md", '---\npaths: ["frontend/**"]\n---\n\n# 前端\n')
        # The block sequence at column 0: the same YAML as the indented spelling above, and
        # the one a rule parsed as unscoped would be injected for every change there is.
        write(".claude/rules/elsewhere.md", '---\npaths:\n- "cli/**"\n---\n\n# 命令行\n')
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True,
                       capture_output=True)
        base = _git(repo, "rev-parse", "HEAD").strip()

        def from_(parent, pairs):
            """Commit `pairs` (path, content) directly on top of `parent`, and return it.
            Each case gets a whole range of its own: a commit stacked on the last case's
            would drag that case's files into this case's diff."""
            subprocess.run(["git", "-C", str(repo), "reset", "-q", "--hard", parent],
                           check=True, capture_output=True)
            for rel, text in pairs.items():
                write(rel, text)
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "change"], check=True,
                           capture_output=True)
            return _git(repo, "rev-parse", "HEAD").strip()

        def case(name, pairs, want, parent=None):
            head = from_(parent or base, pairs)
            check(name, classify(repo, parent or base, head)[0], want)
            return head

        case("lock 文件单独改，是 G",
             {"frontend/pnpm-lock.yaml": "lockfileVersion: 2\n"}, "G")
        case("只加注释，是 G", {"f.py": "x = 1\n# why\n"}, "G")
        case("只改空格，是 G", {"f.py": "x  =  1\n"}, "G")
        # Indentation is syntax in Python and YAML: moving a line in or out of a block
        # changes what runs or what the document says. In a brace language it does not.
        case("Python 里把 return 移出 if，不是 G",
             {"f.py": "def f(x):\n    if x:\n        y = 1\n    return 2\n"}, "B",
             parent=from_(base, {"f.py": "def f(x):\n    if x:\n        y = 1\n        return 2\n"}))
        case("YAML 改缩进会改结构，不是 G", {"f.yml": "a:\n  b: 1\n  c: 2\n"}, "B",
             parent=from_(base, {"f.yml": "a:\n  b: 1\nc: 2\n"}))
        case("TypeScript 只改缩进，是 G", {"f.ts": "if (x) {\n    y = 1;\n}\n"}, "G",
             parent=from_(base, {"f.ts": "if (x) {\n  y = 1;\n}\n"}))
        case("注释里带引号里的井号，仍算注释行", {"f.py": 'x = 1\n# see "a # b"\n'}, "G")
        case("加一行代码，是 B", {"f.py": "x = 1\ny = 2\n"}, "B")
        case("字符串里出现井号的那一行，不算注释", {"f.py": 'x = "# not a comment"\n'}, "B")
        case("Go 里以 * 开头的一行代码，不是注释", {"f.go": "*p = 3\n"}, "B")
        case("前端块注释的续行判不出来，按路径定档", {"f.ts": " * 说明\n"}, "B")

        # Two files that each need reading, of different tiers: the change is reviewed at
        # the highest of them — not the one seen first, and not the lightest.
        case("A 档和 B 档各改一个文件，取最高的 A",
             {"backend/app/auth/caller.py": "ok = False\n",
              "backend/app/domain/review/pr_text.py": "x = 1\n"}, "A")
        case("B 档和 E 档各改一个文件，取最高的 B",
             {"backend/app/domain/review/pr_text.py": "y = 2\n", "AGENTS.md": "rules\n"},
             "B")
        # A path holding a non-ASCII byte: git quotes it in `--name-only` unless quoting is
        # off, and a quoted path matches no prefix — it reads as B, the 判不了 answer. For a
        # doc that is heavier than the E it is, but for an auth or migration path it is
        # lighter than the A it must be, and that direction is the one that matters.
        # Two mechanisms answer that — the `-c` in _git and the `-z` in paths_of — and the
        # two checks below pin one each, so dropping either turns a case red.
        non_ascii = case("非 ASCII 路径的文档，仍按 E 档",
                         {"docs/中文说明.md": "装法见 README\n"}, "E")
        check("_git 自己列非 ASCII 路径时也不加引号",
              _git(repo, "diff", "--name-only", base, non_ascii).strip(), "docs/中文说明.md")
        case("非 ASCII 路径的鉴权代码，仍按 A 档",
             {"backend/app/auth/调用者.py": "ok = False\n"}, "A")
        # What only `-z` can carry: a path holding a quote. git C-quotes that one whatever
        # `core.quotePath` says, so without `-z` it arrives as a string matching no prefix and
        # a doc reads as B. Not every system can hold the name (Windows forbids the quote),
        # so the case is written where the name can be made, and said to be skipped where it
        # cannot rather than passing quietly.
        try:
            case('带引号的文档路径，仍按 E 档', {'docs/引"号".md': "装法见 README\n"}, "E")
        except OSError:
            print("skip 带引号的文档路径：这个系统建不了含引号的文件名")
        # Deleting is not adding: a removed line has no twin on the other side, so a
        # removed guard or a dropped file still gets read. Each case needs a parent of its
        # own, because a deletion that reverts the file to a version it already had is a
        # clean revert (G) by the rule below, and that is not what is being tested here.
        del_parent = from_(base, {"f.py": "x = 1\ny = 2\nz = 3\n"})
        case("删掉一行代码，不是机械改动", {"f.py": "x = 1\nz = 3\n"}, "B", parent=del_parent)
        case("把一行代码挪个位置会改执行顺序，不是 G", {"f.py": "y = 2\nz = 3\nx = 1\n"}, "B",
             parent=del_parent)
        rm_parent = from_(base, {"f.py": "x = 1\n", "g.py": "g = 1\n"})
        (repo / "g.py").unlink()
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "drop g.py"], check=True,
                       capture_output=True)
        check("删掉一整份代码文件，是 B",
              classify(repo, rm_parent, _git(repo, "rev-parse", "HEAD").strip())[0], "B")

        revert_base = from_(base, {"f.py": "x = 1\ny = 2\n"})
        case("干净 revert，是 G", {"f.py": "x = 1\n"}, "G", parent=revert_base)
        case("改回一个没出现过的内容，不是 revert", {"f.py": "x = 3\n"}, "B", parent=revert_base)
        case("锁文件配上别的文件，取高的那档",
             {"frontend/pnpm-lock.yaml": "lockfileVersion: 3\n", "x.py": "y = 1\n"}, "B",
             parent=revert_base)
        case("改一角鉴权代码，仍按 A 档审",
             {"backend/app/auth/caller.py": "# 注释\nok = True\n"}, "A", parent=revert_base)
        case("迁移文件只加注释，也按 A 档审",
             {"backend/alembic/versions/v.py": "revision = 'v'\n# why\n"}, "A", parent=revert_base)
        case("生成文件只动一行，是 G",
             {"frontend/src/api/openapi.d.ts": "export type A = 2;\n"}, "G")
        case("一个机械文件加一份文档，是 E",
             {"frontend/pnpm-lock.yaml": "lockfileVersion: 4\n", "AGENTS.md": "hi\n"}, "E")

        # The B fast path: a change small enough for one reader spends one finder, and its
        # letter stays B. The cases that expect a tier's own count read it from the tier
        # instead of naming it: a literal here would be a second copy of the table, and the
        # guard that widens a stage and moves this file's own expectations along would miss
        # it — `.claude/scripts/test_check_agent_defs.py` mutates those three spots and
        # requires the result to stay green, so what is asserted here is "not the fast
        # path", not "two".
        def fast_case(name, pairs, want, parent=None):
            head = from_(parent or base, pairs)
            letter, _, top = classify(repo, parent or base, head)
            check(name, (letter, top["finders"]), want)
            return head

        not_fast = topology("B")["finders"]
        fast_case("一行实现改动的 B 档走单路快路径", {"small.py": "x = 1\n"}, ("B", 1))
        fast_case("七个文件的 B 档升回两路（故意违规必须红）",
                  {f"m{i}.py": "x = 1\n" for i in range(7)}, ("B", not_fast))
        fast_case("六个文件刚好在快路径上",
                  {f"n{i}.py": "x = 1\n" for i in range(6)}, ("B", 1))
        fast_case("二百五十行的改动刚好在快路径上",
                  {"edge.py": "".join(f"v{i} = 1\n" for i in range(250))}, ("B", 1))
        fast_case("二百五十一行超出快路径",
                  {"wide.py": "".join(f"v{i} = 1\n" for i in range(251))}, ("B", not_fast))
        fast_case("碰 Windows 专属文件，不走快路径",
                  {"cli/internal/procscreen/system_windows.go": "package x\n"}, ("B", not_fast))
        fast_case("碰远程执行，不走快路径",
                  {"scripts/remote_execution/run.py": "x = 1\n"}, ("B", not_fast))
        fast_case("碰契约层，不走快路径",
                  {"backend/app/domain/x.py": "x = 1\n",
                   "backend/tests/contract/test_x.py": "def test_x():\n    pass\n"},
                  ("B", not_fast))
        fast_case("碰全仓棘轮基线，不走快路径",
                  {"frontend/stylelint-baseline.json": "{}\n"}, ("B", not_fast))
        fast_case("A 档没有快路径",
                  {"backend/app/auth/caller.py": "ok = False\n"},
                  ("A", topology("A")["finders"]))
        fast_case("只改测试仍是 C 档，一路",
                  {"backend/tests/unit/test_x.py": "x = 1\n"}, ("C", 1))

        head = from_(base, {"f.py": "x = 1\n# hi\n"})
        check("机械改动写明为什么", classify(repo, base, head)[1],
              "f.py（只动注释、空格或缩进，或是干净 revert）")
        check("没有改到文件时是 G", classify(repo, base, base)[0], "G")
        check("读不到 range 时按 B", classify(repo, "deadbeef", base)[0], "B")

        # What prepare injects: the rules a change loads, read at the rev it was given.
        check("改到后端测试，注入后端的 AGENTS.md 和两条命中的规则",
              rule_files(repo, base, ["backend/tests/unit/test_x.py", "frontend/src/a.ts"]),
              ["backend/AGENTS.md", ".claude/rules/always.md",
               ".claude/rules/backend-tests.md", ".claude/rules/frontend.md"])
        check("只改后端源码，只剩 AGENTS.md 与没写 paths 的那条规则",
              rule_files(repo, base, ["backend/app/domain/x.py"]),
              ["backend/AGENTS.md", ".claude/rules/always.md"])
        check("仓库根的 AGENTS.md 不注入", "AGENTS.md" in
              rule_files(repo, base, ["backend/app/domain/x.py"]), False)
        check("没注入根 AGENTS.md 是因为刻意跳过，不是它不在", _exists(repo, base, "AGENTS.md"),
              True)
        check("改到前端，注入前端那条规则", rule_files(repo, base, ["frontend/src/a.vue"]),
              [".claude/rules/always.md", ".claude/rules/frontend.md"])
        check("顶格写 paths 的规则在别的子树下不算没写 paths",
              ".claude/rules/elsewhere.md" in rule_files(repo, base, ["backend/app/domain/x.py"]),
              False)
        check("顶格写 paths 的规则命中时照样注入",
              ".claude/rules/elsewhere.md" in rule_files(repo, base, ["cli/main.py"]), True)
        check("读不到 rev 时一条都不注入", rule_files(repo, "deadbeef", ["f.py"]), [])
        # The rules come from the rev prepare passes, so a change that rewrites a rule
        # cannot rewrite what its own review is handed. Here the PR points the frontend
        # rule at backend paths: at its own head it no longer covers the file it changed,
        # which is the reading prepare must never take.
        rule_head = from_(base, {".claude/rules/frontend.md":
                                 '---\npaths: ["backend/**"]\n---\n\n# 前端\n'})
        check("按默认分支读：前端改动仍拿到前端规则",
              ".claude/rules/frontend.md" in rule_files(repo, base, ["frontend/src/a.vue"]), True)
        check("按这个改动自己的 head 读：规则被改动后就不命中了",
              ".claude/rules/frontend.md" in rule_files(repo, rule_head, ["frontend/src/a.vue"]),
              False)

        check("参数不认识就报错，不当作没给",
              main(["--repo", str(repo), "--base", base, "--head", base, "--rules", "--letter"]), 2)
        check("值缺失也算参数错",
              main(["--repo", str(repo), "--base", base, "--head"]), 2)
        check("认不出来的开关也报错",
              main(["--repo", str(repo), "--base", base, "--head", base, "--tier"]), 2)

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import orchestrator_autoaccept as aa

        check("A 档的路径表和自动采纳脚本的高风险表一致", HIGH_RISK, aa.HIGH_RISK)
        check("A 档的路径表和自动采纳脚本的正式部署表一致", PROD_DEPLOY, aa.PROD_DEPLOY)
    except ImportError:
        print("self-test: 自动采纳脚本不在，跳过两张表的对拍", file=sys.stderr)

    for line in bad:
        print("FAIL " + line)
    if bad:
        print(f"self-test: {len(bad)} of {total} failed")
        raise SystemExit(1)
    print(f"self-test: ok ({total} cases, offline)")


def main(argv):
    """Strict about its own arguments, unlike the `--flag value` pairing this once read:
    that dict swallowed anything it did not know, so a copy of this file predating `--rules`
    would have answered an unknown `--rules` with the tier block, which prepare would then
    print into REVIEW.md as if it were the rule list."""
    if "--self-test" in argv:
        return self_test()
    opts = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--repo", "--base", "--head", "--rev"):
            if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
                print(f"{arg} 后面少一个值", file=sys.stderr)
                return 2
            opts[arg] = argv[i + 1]
            i += 2
        elif arg in ("--letter", "--rules"):
            opts[arg] = True
            i += 1
        else:
            print(f"不认识的参数：{arg}", file=sys.stderr)
            return 2
    if "--letter" in opts and "--rules" in opts:
        print("--letter 和 --rules 只能给一个", file=sys.stderr)
        return 2
    repo, base, head = opts.get("--repo"), opts.get("--base"), opts.get("--head")
    if not (repo and base and head):
        print(__doc__, file=sys.stderr)
        return 2
    if "--rules" in opts:
        changed = paths_of(repo, "--no-renames", base, head)
        if changed is None:
            return 0
        for name in rule_files(repo, opts.get("--rev") or DEFAULT_REV, changed):
            print(name)
        return 0
    letter, detail, top = classify(repo, base, head)
    # `--letter` prints the letter alone: pr-review.sh keeps it for the conclusion line,
    # where a B change on the fast path is still a B. The block is what the reviewer reads,
    # so that one carries the topology this change actually spends.
    print(letter if "--letter" in opts else block(letter, detail, top))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
