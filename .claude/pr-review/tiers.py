#!/usr/bin/env python3
"""Which tier of independent review a change needs, from the files it touches.

    python3 .claude/pr-review/tiers.py --repo ROOT --base BASE --head HEAD
    python3 .claude/pr-review/tiers.py --repo ROOT --base BASE --head HEAD --letter
    python3 .claude/pr-review/tiers.py --self-test

`.claude/scripts/pr-review.sh prepare` runs this, prints the block into REVIEW.md and
keeps the letter for the card's first line (`render.py --tier`). The tiers and their
criteria are 《总调度工作流 v2》 §4.

A change takes the highest tier any of its files falls in — the table below is ranked,
A first. G is not in that ranking: a change is G only when EVERY file is mechanical, and
a file the script cannot call mechanical is judged by its path. 判不了按 B, because B is
what asks a person to read the change; granting G on a guess would skip the reading.

The A paths are the same vocabulary `.claude/scripts/orchestrator_autoaccept.py` gates
auto-accept on (its HIGH_RISK and PROD_DEPLOY): a change too risky to accept unattended
must not be a lighter tier here either. `--self-test` fails when the two lists drift.
"""

import subprocess
import sys
from pathlib import Path

#: The ranked table: (letter, name, what it covers, what to review, how deep). The criteria
#: text is handed to the reviewer as-is; the doc's "who reviews" column is not here, because
#: a seat running a lighter tier still decides how many subagents to spend on it.
TIERS = (
    (
        "A",
        "高风险",
        "迁移、鉴权、部署链、计量代理、用户可见文案",
        "可逆性、鉴权边界、影响面、回滚方式；文案看会不会改变用户已定的状态",
        "两路找茬 + 逐条证伪；碰正式部署一律人定",
    ),
    (
        "B",
        "实现代码",
        "后端、前端逻辑，API，脚本",
        "做没做对需求、可达路径上的错行为、边界与并发、复杂度；每条问题带 文件:行",
        "两路全新上下文找茬，各自给结论，再逐条证伪只留站得住的",
    ),
    (
        "C",
        "测试",
        "只改测试、夹具、守卫自测",
        "测试会不会咬人：一次变异必须红；断言两边是否同源",
        "一路，重点跑变异",
    ),
    (
        "D",
        "配置与 CI",
        "workflow、pre-commit、settings、路由表",
        "影响面、密钥有没有写进去、能不能回滚；与代码改动拆开",
        "一路，读全 diff",
    ),
    (
        "E",
        "文档与说明",
        "AGENTS.md、.claude/rules、技能、README",
        "每句问「删掉会不会出错」；有没有已被 lint 或测试拦着的；引用的命令和路径存不存在",
        "一路，只读",
    ),
)
MECHANICAL = (
    "G",
    "机械改动",
    "纯注释、纯格式、lock、生成文件、干净 revert",
    "确认确实是机械的",
    "不审，CI 过即可",
)

#: 迁移、鉴权、部署链、用户可见文案，as (what, prefix) — identical to
#: orchestrator_autoaccept.HIGH_RISK, which the self-test checks.
HIGH_RISK = (
    ("迁移", "backend/alembic/"),
    ("鉴权", "backend/app/auth/"),
    ("鉴权", "backend/app/api/auth.py"),
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
    "deploy/docker-compose.prod.yml",
    "deploy/.env.prod.example",
    "deploy/compose/",
)
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
COMMENT = {
    ".py": ("#",),
    ".sh": ("#",),
    ".yml": ("#",),
    ".yaml": ("#",),
    ".toml": ("#",),
    ".ini": ("#",),
    ".cfg": ("#",),
    ".ts": ("//", "/*", "*"),
    ".tsx": ("//", "/*", "*"),
    ".js": ("//", "/*", "*"),
    ".jsx": ("//", "/*", "*"),
    ".mjs": ("//", "/*", "*"),
    ".cjs": ("//", "/*", "*"),
    ".scss": ("//", "/*", "*"),
    ".css": ("/*", "*"),
    ".go": ("//", "/*", "*"),
    ".rs": ("//", "/*", "*"),
    ".sql": ("--",),
    ".vue": ("<!--", "//", "/*", "*"),
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
    return (
        name.endswith((".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx",
                       ".test.ts", ".test.tsx", ".test.js", ".test.jsx"))
        or (name.startswith("test_") and name.endswith(".py"))
        or name.endswith("_test.py")
        or "/tests/" in path
        or "/test/" in path
        or "__tests__/" in path
        or path.startswith("e2e/")
        or "/e2e/" in path
    )


def is_doc(path):
    return path.endswith(".md") or any(under(path, p) for p in DOCS)


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
    out = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    return out.stdout if out.returncode == 0 else None


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
    """Every added and removed line is blank, starts with this file's comment token, or
    differs from a line on the other side only in its spacing — comments, reindentation
    and whitespace-only edits, which is what §4's 纯注释 and 纯格式 come to in a diff.

    Both sides are compared with all whitespace taken out: an added line that has a twin
    among the removed lines is the same line moved or re-indented. A file type with no
    entry in COMMENT is not judged here (False), not assumed clean."""
    tokens = COMMENT.get(Path(path).suffix)
    if not tokens:
        return False
    diff = _git(repo, "diff", "-U0", "--no-color", base, head, "--", path)
    if diff is None:
        return False
    added, removed = [], []
    for line in diff.splitlines():
        if line.startswith(("+++", "---", "@@", "diff ", "index ", "new file", "deleted ",
                            "similarity ", "rename ", "old mode", "new mode")):
            continue
        if line[:1] == "+":
            added.append(line[1:])
        elif line[:1] == "-":
            removed.append(line[1:])
    if not added and not removed:
        return False
    stripped_removed = ["".join(l.split()) for l in removed]
    for raw in added:
        body = raw.strip()
        if not body or body.startswith(tokens):
            continue
        if "".join(raw.split()) in stripped_removed:
            continue
        return False
    return True


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


def classify(repo, base, head):
    """(letter, detail) for the change `base`..`head` — the whole PR, so a delta review
    still reports the tier of what is being merged."""
    listed = _git(repo, "diff", "--name-only", "--no-renames", base, head)
    if listed is None:
        return "B", "读不到改动的文件，按 B 档"
    paths = [p for p in listed.splitlines() if p.strip()]
    if not paths:
        return "G", "没有改到任何文件"
    hard = [p for p in paths if not mechanical(repo, base, head, p)]
    if not hard:
        return "G", "、".join(f"{p}（{_mechanical_why(p)}）" for p in sorted(paths)[:6])
    rank = {t[0]: i for i, t in enumerate(TIERS)}
    letter = min((tier_of_path(p) for p in hard), key=lambda t: rank[t])
    drivers = sorted(p for p in hard if tier_of_path(p) == letter)
    return letter, "、".join(f"{p}（{_why(p, letter)}）" for p in drivers[:4])


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


def block(letter, detail):
    """The markdown `prepare` puts in REVIEW.md: the tier, why this change is in it, and
    the criteria of that tier as the doc words them."""
    _, name, covers, criteria, depth = row(letter)
    head = f"**档位 {letter} {name}**（{covers}）"
    if letter == "G":
        head += "——免审，CI 过即可"
    return "\n".join((head, f"- 为什么会是这一档：{detail}",
                      f"- 审什么：{criteria}", f"- 审多深：{depth}"))


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
        ("frontend/src/views/Home.spec.ts", "C"),
        ("e2e/tests/task.spec.ts", "C"),
        (".github/workflows/ci-fast.yml", "D"),
        ("backend/scripts/route_index.json", "D"),
        ("AGENTS.md", "E"),
        (".claude/rules/migrations.md", "E"),
        ("docs/accept-is-merge.md", "E"),
        ("deploy/README-backup.md", "A"),  # deploy/ wins over .md: A is tested first
    ):
        check(f"按路径定档：{path}", tier_of_path(path), want)

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
        case("只改缩进，是 G", {"f.py": "if x:\n    y = 1\n"}, "G",
             parent=from_(base, {"f.py": "if x:\ny = 1\n"}))
        case("注释里带引号里的井号，仍算注释行", {"f.py": 'x = 1\n# see "a # b"\n'}, "G")
        case("加一行代码，是 B", {"f.py": "x = 1\ny = 2\n"}, "B")
        case("字符串里出现井号的那一行，不算注释", {"f.py": 'x = "# not a comment"\n'}, "B")

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

        head = from_(base, {"f.py": "x = 1\n# hi\n"})
        check("机械改动写明为什么", classify(repo, base, head)[1],
              "f.py（只动注释、空格或缩进，或是干净 revert）")
        check("没有改到文件时是 G", classify(repo, base, base)[0], "G")
        check("读不到 range 时按 B", classify(repo, "deadbeef", base)[0], "B")

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
    if "--self-test" in argv:
        return self_test()
    args = dict(zip((a for a in argv if a.startswith("--")),
                    (a for a in argv if not a.startswith("--"))))
    repo, base, head = args.get("--repo"), args.get("--base"), args.get("--head")
    if not (repo and base and head):
        print(__doc__, file=sys.stderr)
        return 2
    letter, detail = classify(repo, base, head)
    print(letter if "--letter" in argv else block(letter, detail))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
