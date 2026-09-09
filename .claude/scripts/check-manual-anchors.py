#!/usr/bin/env python3
"""check-manual-anchors.py — the user manual's anchors must be stable and real.

WHY THIS EXISTS: the manual is meant to be answerable by 芝士 — a person asks
"how do I connect my repo", and the answer comes back with the URL of the
section it came from. Two failures make that worse than useless, and both are
silent:

  1. A heading's anchor is generated from its text. Someone improves the wording
     of 「连接你的仓库」 and every link ever handed out for it 404s — no build
     breaks, no test goes red, and the only symptom is a reader landing at the
     top of the page wondering where the section went. So every heading declares
     its own slug (`## 连接仓库 {#connect-repo}`) and the slug is what URLs are
     built from; the title is then free to change.

  2. The list of anchors 芝士 is given (anchors.json) drifts from the manual.
     A model handed a stale list does not say "I don't have that" — it says the
     nearest thing, with a URL that no longer resolves. So the list is generated
     from the manual, never edited by hand, and this guard fails when the
     checked-in copy is not what the manual currently produces.

The one-line summary in that list is the section's FIRST SENTENCE, taken as-is.
That is a writing rule, not a parser limitation: whatever you put first is what
a reader (or 芝士) sees when deciding whether this section answers the question.

Usage: check-manual-anchors.py             check (default: repo's docs/manual)
       check-manual-anchors.py --write     regenerate anchors.json
       check-manual-anchors.py --self-test prove it catches what it claims to
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SITE_BASE = "/docs"
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*(?:\{#([^}]*)\})?\s*$")
SLUG_OK = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# A link into the manual's own pages: /docs/quickstart#connect-repo
INTERNAL_LINK = re.compile(rf"\]\(({re.escape(SITE_BASE)}/[^)\s]*)\)")


def _first_sentence(lines: list[str]) -> str:
    """The section's opening prose, trimmed to one sentence.

    Skips list items, quotes and fences: a section that opens with a table of
    options has no summary sentence, and inventing one from a bullet reads as a
    fact about the whole section when it is a fact about one row.
    """
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(("#", "-", "*", ">", "|", "```", "<!--")):
            continue
        text = re.sub(r"\*\*|`|\[|\]\([^)]*\)", "", line)
        parts = re.split(r"(?<=[。！？])", text)
        return (parts[0] if parts[0].strip() else text).strip()
    return ""


def parse_page(path: Path) -> tuple[str, list[dict[str, str]], list[str]]:
    """Return (page slug, its anchors, the internal links it makes)."""
    body = path.read_text(encoding="utf-8")
    page_slug = path.stem
    lines = body.splitlines()

    anchors: list[dict[str, str]] = []
    problems: list[str] = []
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if not m:
            continue
        hashes, title, slug = m.group(1), m.group(2), m.group(3)
        where = f"{path.name}:{i + 1}"
        if not slug:
            problems.append(f"{where}: 标题没有显式 slug — 写成 `{hashes} {title} {{#some-slug}}`")
            continue
        if not SLUG_OK.match(slug):
            problems.append(f"{where}: slug `{slug}` 只能是小写字母、数字和连字符")
            continue
        anchors.append(
            {
                "url": f"{SITE_BASE}/{page_slug}#{slug}",
                "page": page_slug,
                "title": title,
                "summary": _first_sentence(lines[i + 1 :]),
            }
        )

    links = [m.group(1) for m in INTERNAL_LINK.finditer(body)]
    return page_slug, anchors, problems + [f"__links__{link}" for link in links]


def build(manual_dir: Path) -> tuple[dict[str, object], list[str]]:
    anchors: list[dict[str, str]] = []
    problems: list[str] = []
    links: list[str] = []
    pages = sorted(p for p in manual_dir.glob("*.md") if p.name != "README.md")
    if not pages:
        return {}, [f"{manual_dir} 下没有任何手册页面 — 什么都没检查，这本身就是失败"]
    for path in pages:
        _, page_anchors, raw = parse_page(path)
        anchors.extend(page_anchors)
        for item in raw:
            (links if item.startswith("__links__") else problems).append(
                item.removeprefix("__links__")
            )

    seen: dict[str, str] = {}
    for a in anchors:
        if a["url"] in seen:
            problems.append(f"重复的锚点 {a['url']}（`{seen[a['url']]}` 和 `{a['title']}`）")
        seen[a["url"]] = a["title"]
        if not a["summary"]:
            problems.append(f"{a['url']}: 这一节没有开头的说明句 — 芝士引用它时没有摘要可给")

    known = set(seen)
    for link in links:
        if link.split("#")[0].rstrip("/") == SITE_BASE:  # a bare page link
            continue
        if link not in known:
            problems.append(f"文档里链到了不存在的锚点：{link}")

    return {"base": SITE_BASE, "anchors": anchors}, problems


def render(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def run(manual_dir: Path, *, write: bool) -> int:
    payload, problems = build(manual_dir)
    if problems:
        print("FAIL: 用户手册的锚点有问题：")
        for p in problems:
            print(f"  {p}")
        print("::error::manual anchors are not stable/real (see above)")
        return 1

    target = manual_dir / "anchors.json"
    expected = render(payload)
    if write:
        target.write_text(expected, encoding="utf-8")
        print(f"WROTE: {target} — {len(payload['anchors'])} 个锚点")  # type: ignore[arg-type]
        return 0
    actual = target.read_text(encoding="utf-8") if target.exists() else ""
    if actual != expected:
        print(f"FAIL: {target} 和手册对不上 — 跑 `{Path(__file__).name} --write` 重新生成")
        print("::error::anchors.json is stale (芝士 would hand out URLs from it)")
        return 1
    print(f"PASS: {len(payload['anchors'])} 个锚点都是显式的、唯一的、链得到的")  # type: ignore[arg-type]
    return 0


def self_test() -> int:
    import contextlib
    import io
    import tempfile

    fails: list[str] = []

    def quiet_run(d: Path) -> int:
        """run() prints its own verdict; inside the self-test that is noise."""
        with contextlib.redirect_stdout(io.StringIO()):
            return run(d, write=False)

    def case(name: str, files: dict[str, str], should_pass: bool) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for fname, content in files.items():
                (d / fname).write_text(content, encoding="utf-8")
            payload, problems = build(d)
            if should_pass and problems:
                fails.append(f"{name}: 本该通过，却报了 {problems}")
            if not should_pass and not problems:
                fails.append(f"{name}: 本该失败，却通过了")
            if should_pass and not problems:
                (d / "anchors.json").write_text(render(payload), encoding="utf-8")
                if quiet_run(d) != 0:
                    fails.append(f"{name}: 生成的 anchors.json 自己对不上")

    good = "# 标题 {#top}\n\n开头一句话。\n\n## 小节 {#sec}\n\n这一节讲什么。\n"
    case("显式 slug + 摘要", {"quickstart.md": good}, True)
    case("标题没有 slug", {"quickstart.md": "# 标题\n\n一句话。\n"}, False)
    case("slug 用了大写", {"quickstart.md": "# 标题 {#Top}\n\n一句话。\n"}, False)
    case(
        "两页撞了同一个锚点",
        {"a.md": "# A {#x}\n\n一句话。\n", "b.md": "# B {#x}\n\n一句话。\n"},
        True,  # 不同页面 → /docs/a#x 与 /docs/b#x，本就不冲突
    )
    case("同一页重复 slug", {"a.md": "# A {#x}\n\n一。\n\n## B {#x}\n\n二。\n"}, False)
    case("小节没有说明句", {"a.md": "# A {#x}\n\n- 只有列表\n"}, False)
    case(
        "链到不存在的锚点",
        {"a.md": "# A {#x}\n\n见 [那边](/docs/a#nope)。\n"},
        False,
    )
    case(
        "链到存在的锚点",
        {"a.md": "# A {#x}\n\n见 [那边](/docs/a#y)。\n\n## Y {#y}\n\n一句话。\n"},
        True,
    )
    case("一个页面都没有", {}, False)

    # 陈旧的 anchors.json 必须被抓到 —— 这是这个守卫存在的第二个理由。
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "quickstart.md").write_text(good, encoding="utf-8")
        (d / "anchors.json").write_text('{"base": "/docs", "anchors": []}\n', encoding="utf-8")
        if quiet_run(d) == 0:
            fails.append("陈旧的 anchors.json: 本该失败，却通过了")

    if fails:
        for f in fails:
            print(f"SELF-TEST FAIL: {f}", file=sys.stderr)
        return 1
    print("PASS: check-manual-anchors self-test（缺 slug / 大写 / 重复 / 无摘要 / 死链 / 陈旧清单 / 空目录）")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test()
    root = Path(__file__).resolve().parents[2]
    manual = root / "docs" / "manual"
    return run(manual, write="--write" in args)


if __name__ == "__main__":
    raise SystemExit(main())
