#!/usr/bin/env python3
"""check-doc-references.py — a doc's path and section claims must be true.

WHY THIS EXISTS: the instructions an agent reads name other files — a command
(`bash .claude/scripts/check-repo-rules.sh`), or "the rules are in
`.claude/rules/architecture.md`" — and an agent follows them literally. When the
target has moved, nothing else notices: no build reads `.claude/`, no test
imports it, and the reader who obeys the sentence gets a "No such file" and has
to guess where the rule went. That is the worst possible outcome for a document
whose whole job is to be obeyed.

The live example this check was built against: `.claude/skills/
cheese-py-code-review/SKILL.md` told every reviewer to check a diff against
"AGENTS.md's **Architecture / Python Conventions / API Design / Datetime /
Security** sections" (quoted as it reads today; it named `CLAUDE.md` when
written). The sentence was correct then: `1c83ebdf` (2026-08-10, #208) added it
while `CLAUDE.md` really did carry all five of those headings. What killed it
was `1b9dc3f7` (#518, 2026-08-18), which cut that file from 363 lines to 55 and
deleted the five sections without touching the sentence pointing at them. The
later rename to `AGENTS.md`
(`40709ff1`, 2026-10-11, #3394) only followed the moved filename in the same
sentence — it is not the cause. So the pointer was dead for 55 days, in the file
a reviewer is told to trust, and nothing noticed.

What is deliberately NOT here:

  * `docs/manual/**` links, image paths and a dev page's `covers:` list — the
    docs site build already fails on all three (`docs/site/build.mjs`). A second
    copy of a rule a script already enforces can only drift away from it.
  * Paths inside `.agents/**` — that tree is vendored skill content, full of
    illustrative paths (`./out.json`, `sub1/sub2/file.txt`) that are correct as
    examples and wrong as files.

A path claim is judged under a few candidate roots rather than one, because the
docs in scope use three conventions at once: repo-root-relative
(`.claude/rules/e2e.md`), subtree-relative (`src/api.ts` in a frontend file) and
file-relative (markdown links, which have exactly one meaning and are resolved
only that way). Being generous is the right trade for a ratchet: it still catches
the defect this exists to catch — the target exists *nowhere* — while a stricter
resolver would fail today's tree and be switched off within a week.

Usage: check-doc-references.py             check
       check-doc-references.py --self-test prove it catches what it claims to
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

#: Roots a bare path claim is tried under, after the file's own directory. The
#: order only affects the reported location, not the verdict.
CANDIDATE_ROOTS = (
    ".",
    "frontend/src",
    "frontend",
    "backend/app",
    "backend/tests",
    "backend",
    "docs",
    ".claude",
)

#: What is scanned: the instructions an agent is given, minus the vendored tree.
SCAN_GLOBS = ("**/AGENTS.md", ".claude/**/*.md")

#: Directories that hold third-party content rather than this repo's docs.
SKIP_PARTS = frozenset({"node_modules", ".git", ".agents", ".venv", "dist"})

EXTENSIONS = (
    ".md", ".py", ".ts", ".tsx", ".vue", ".json", ".sh", ".yml", ".yaml",
    ".mjs", ".cjs", ".mts", ".sass", ".css", ".html", ".toml", ".txt",
)

TOKEN = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./-]*")
BACKTICKED = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\]\(([^)\s]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")
# The broken-pointer shape: `<some file>.md`'s **A / B / C** sections
SECTION_CLAIM = re.compile(
    r"([A-Za-z0-9_./-]+\.md)[’']?s? \*\*([^*]+)\*\*\s+sections?"
)

FIX_HINT = (
    "修法：把报出的路径改成它现在的位置，或把那份文档搬到被引用的地方；"
    "小节名同理 —— 要么改成本文件真实存在的标题，要么把句子改写成分散指向"
    "（`.claude/rules/`、各目录 AGENTS.md、docs/manual/dev/）。"
    "复查命令是 python3 .claude/scripts/check-doc-references.py。"
)


def looks_like_path(tok: str, *, linked: bool = False) -> bool:
    """A repo path claim, as opposed to prose, a URL or a written-out example.

    `linked` marks a markdown link target, where `./x` and `../x` carry one
    meaning and are judged as written — the same shape in backticks is how docs
    write an example (`./out.json`), not a claim about this repo.
    """
    if "/" not in tok or any(c in tok for c in "*<>{}[]"):
        return False
    if "..." in tok or "://" in tok:
        return False
    if tok.startswith("/"):  # a site route (`/quickstart`), not a file
        return False
    segments = tok.split("/")
    if not linked and segments[0] in (".", ".."):
        return False
    first = segments[0]
    if "." in first.lstrip("."):  # `open.feishu.cn/llms.txt` — a host, not a dir
        return False
    return tok.endswith(EXTENSIONS)


def path_claims(text: str):
    """(line, raw claim, is_markdown_link) for every path this file names."""
    for m in LINK.finditer(text):
        raw = m.group(1)
        if raw.startswith(("http", "#", "mailto:")):
            continue
        target = raw.split("#")[0]
        if target and looks_like_path(target, linked=True):
            yield text[: m.start()].count("\n") + 1, target, True
    for m in BACKTICKED.finditer(text):
        for tok in TOKEN.findall(m.group(1)):
            if looks_like_path(tok):
                yield text[: m.start()].count("\n") + 1, tok, False


def resolve(claim: str, source: Path, root: Path) -> Path | None:
    """Where a claim lands, or None. A markdown link means one place only."""
    if claim in (".", ".."):
        return None
    for candidate in (root / claim, source.parent / claim):
        if candidate.exists():
            return candidate
    for base in CANDIDATE_ROOTS:
        candidate = root / base / claim
        if candidate.exists():
            return candidate
    return None


def resolve_link(claim: str, source: Path, root: Path) -> Path | None:
    target = (source.parent / claim).resolve()
    return target if target.exists() else None


def find_doc(name: str, root: Path) -> Path | None:
    for base in (root, *(root / b for b in CANDIDATE_ROOTS)):
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


def headings(path: Path) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = HEADING.match(line)
        if m:
            out.append(re.sub(r"\s*\{#.*?\}\s*$", "", m.group(1)).strip())
    return out


def files_to_scan(root: Path) -> list[Path]:
    found: set[Path] = set()
    for pattern in SCAN_GLOBS:
        for path in root.glob(pattern):
            if path.is_file() and not (SKIP_PARTS & set(path.parts)):
                found.add(path)
    return sorted(found)


def check(files: list[Path], root: Path) -> tuple[list[str], int, int]:
    """(problems, path claims judged, section claims judged).

    No files is a failure, not a pass: a scope that matched nothing scans
    nothing, and a guard that scans nothing looks exactly like a clean tree.
    """
    if not files:
        return ["一份 agent 文档都没扫到 — 什么都没检查，这本身就是失败"], 0, 0
    problems: list[str] = []
    claims = sections = 0
    for path in files:
        where = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")

        for line, claim, is_link in path_claims(text):
            claims += 1
            found = (
                resolve_link(claim, path, root)
                if is_link
                else resolve(claim, path, root)
            )
            if found is None:
                problems.append(
                    f"{where}:{line}: 写到的路径不存在：{claim}；"
                    + ("链接按文件所在目录解，其余按仓库根/子树根解" if is_link else
                       "改成它现在的位置，或先建出这个文件")
                )

        flat = re.sub(r"\s+", " ", text)
        for m in SECTION_CLAIM.finditer(flat):
            doc_name = m.group(1)
            names = [n.strip().strip("*")
                     for n in re.split(r"\s*/\s*|\s*,\s*", m.group(2))]
            names = [n for n in names if n]
            sections += len(names)
            target = find_doc(doc_name, root)
            if target is None:
                problems.append(
                    f"{where}: 说的 {doc_name} 这个文件不存在"
                    f"（却引用了它的 {len(names)} 个小节名）"
                )
                continue
            existing = headings(target)
            for name in names:
                # 包含而非相等：标题常带后缀（`## Caps and conventions`），
                # 引用的往往只是它前面那截。松一点只会漏报，不会误报。
                if any(name.lower() in h.lower() for h in existing):
                    continue
                shown = "、".join(existing[:3]) + ("…" if len(existing) > 3 else "")
                problems.append(
                    f"{where}: {doc_name} 没有小节「{name}」"
                    f"（它现有 {len(existing)} 个标题：{shown or '一个都没有'}）"
                )
    return problems, claims, sections


def run(root: Path) -> int:
    files = files_to_scan(root)
    problems, claims, sections = check(files, root)
    if problems:
        print("FAIL: 文档里写到的路径或小节名不存在：")
        for problem in problems:
            print(f"  {problem}")
        print(f"  {FIX_HINT}")
        print("::error::a doc names a path or section name that does not exist")
        return 1
    print(
        f"PASS: {len(files)} 份 agent 文档里，{claims} 条路径和 {sections} 条小节名都指得着"
    )
    return 0


def self_test() -> int:
    import contextlib
    import io
    import tempfile

    fails: list[str] = []

    def case(name: str, files: dict[str, str], should_pass: bool) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for fname, content in files.items():
                (root / fname).parent.mkdir(parents=True, exist_ok=True)
                (root / fname).write_text(content, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                code = run(root)
            report = captured.getvalue()
            problems, _, _ = check(files_to_scan(root), root)
            if should_pass and problems:
                fails.append(f"{name}: 本该通过，却报了 {problems}")
            if not should_pass and not problems:
                fails.append(f"{name}: 本该失败，却通过了")
            if should_pass and code != 0:
                fails.append(f"{name}: 没有问题却退出 {code}")
            if not should_pass and code != 1:
                fails.append(f"{name}: 没有保持失败退出码 1（退出 {code}）")
            if not should_pass:
                for needle in ("修法：", "python3 .claude/scripts/check-doc-references.py"):
                    if needle not in report:
                        fails.append(f"{name}: 失败提示缺 {needle!r}")

    good_rule = (
        "改完跑 `bash .claude/scripts/check-repo-rules.sh`；"
        "接手见 `frontend/src/api.ts` 与 [设计系统](../../docs/design-system.md)。\n"
        "判据见 AGENTS.md's **Test behaviour, never the implementation** sections。\n"
    )
    case(
        "路径与小节名都存在",
        {
            "AGENTS.md": "# Root\n\n## Test behaviour, never the implementation\n\n正文。\n",
            "docs/design-system.md": "# 设计系统\n",
            "frontend/src/api.ts": "export {}\n",
            ".claude/scripts/check-repo-rules.sh": "#!/usr/bin/env bash\n",
            ".claude/rules/frontend.md": good_rule,
        },
        True,
    )
    # 这一条就是本检查上线时抓到的真实违规，原样种子（去掉自动加粗的空格）。
    case(
        "引用不存在的小节名（真实违规的形状）",
        {
            "AGENTS.md": "# Project Conventions\n\n## Read the issues before the docs\n",
            ".claude/skills/x/SKILL.md": (
                "Check the diff against every rule in AGENTS.md's **Architecture / Python\n"
                "Conventions / API Design / Datetime / Security** sections.\n"
            ),
        },
        False,
    )
    # 斜杠并列要拆开逐个判，否则「Architecture / Python Conventions …」会被
    # 当成一个名字，报出一条牛头不对马嘴的「没有小节」而漏掉其余四条。
    case(
        "几个小节名用斜杠并列",
        {
            "AGENTS.md": (
                "# Root\n\n## Read the issues before the docs\n\n"
                "## Test behaviour, never the implementation\n"
            ),
            ".claude/skills/x/SKILL.md": (
                "见 AGENTS.md's **Read the issues before the docs / Test behaviour, "
                "never the implementation** sections。\n"
            ),
        },
        True,
    )
    case(
        "小节名大小写不同也认",
        {
            "AGENTS.md": "# Root\n\n## Legal texts are not the work\n",
            ".claude/skills/x/SKILL.md":
                "判据见 AGENTS.md's **LEGAL TEXTS Are Not The Work** sections。\n",
        },
        True,
    )
    case(
        "写到的路径被搬走了",
        {".claude/rules/a.md": "跑 `bash .claude/scripts/gone.sh`。\n"},
        False,
    )
    case(
        "子树根下的相对路径",
        {
            "frontend/src/api.ts": "export {}\n",
            ".claude/rules/a.md": "入口是 `src/api.ts`。\n",
        },
        True,
    )
    case(
        "markdown 链接相对本文件解",
        {
            "docs/design-system.md": "# x\n",
            ".claude/rules/a.md": "见 [设计系统](../../docs/design-system.md#9-动效)。\n",
        },
        True,
    )
    case(
        "markdown 链接指向不存在的文件",
        {".claude/rules/a.md": "见 [设计系统](../../docs/nope.md)。\n"},
        False,
    )
    case(
        "站内路由、域名和写出来的例子不算路径断言",
        {
            ".claude/rules/a.md": (
                "见 [快速开始](/quickstart#top)。\n"
                "写法是 `./out.json`，域名如 open.feishu.cn/llms.txt。\n"
                "资源在 `src/...vue`。\n"
            )
        },
        True,
    )
    case("一份文档都没有", {}, False)

    if fails:
        for f in fails:
            print(f"SELF-TEST FAIL: {f}", file=sys.stderr)
        return 1
    print(
        "PASS: check-doc-references self-test"
        "（不存在的路径 / 被搬走的路径 / 不存在的小节名 / 真实违规种子 / "
        "子树相对 / 文件相对 / 站内路由与占位符不误报 / 空目录）"
    )
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test()
    return run(Path(__file__).resolve().parents[2])


if __name__ == "__main__":
    raise SystemExit(main())
