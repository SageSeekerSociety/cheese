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

  2. A section with no opening sentence has nothing to say about itself. The
     site publishes an `llms.txt` (the convention Anthropic, OpenAI, Stripe,
     Vercel and Linear all serve their docs to models through), and each entry
     there is one line: title, URL, and this section's first sentence. A model
     handed an entry with an empty summary does not say "I don't have that" —
     it guesses from the title. Nothing is checked in: llms.txt is produced by
     scripts/build_manual.py from these same files at build time, so it cannot
     fall behind them.

The one-line summary in that list is the section's FIRST SENTENCE, taken as-is.
That is a writing rule, not a parser limitation: whatever you put first is what
a reader (or 芝士) sees when deciding whether this section answers the question.

Usage: check-manual-anchors.py             check (default: repo's docs/manual)
       check-manual-anchors.py --self-test prove it catches what it claims to
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 源码里的站内链接是 base 之下的路径（`/concepts#task`）——VitePress 自己补
# `/docs/` 前缀，源码里再写一遍会变成 /docs/docs/…（它的死链检查会当场报）。
SITE_BASE = ""
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*(?:\{#([^}]*)\})?\s*$")
SLUG_OK = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# A link into the manual's own pages: /docs/quickstart#connect-repo
# 前面那个 `!` 是分界：`[文字](/x)` 是链到一页，`![说明](/x.png)` 是一张图。
# 两者要分开查 —— 把图片当页面查，加一张图就会被这个守卫拦住（它拦过一次）。
INTERNAL_LINK = re.compile(r"(!?)\[[^\]]*\]\((/[^)\s]*)\)")


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

    refs = [
        ("__assets__" if m.group(1) else "__links__") + m.group(2)
        for m in INTERNAL_LINK.finditer(body)
    ]
    return page_slug, anchors, problems + refs


def build(manual_dir: Path) -> tuple[dict[str, object], list[str]]:
    anchors: list[dict[str, str]] = []
    problems: list[str] = []
    links: list[str] = []
    assets: list[str] = []
    pages = sorted(p for p in manual_dir.glob("*.md") if p.name != "README.md")
    if not pages:
        return {}, [f"{manual_dir} 下没有任何手册页面 — 什么都没检查，这本身就是失败"]
    for path in pages:
        _, page_anchors, raw = parse_page(path)
        anchors.extend(page_anchors)
        for item in raw:
            if item.startswith("__assets__"):
                assets.append(item.removeprefix("__assets__"))
            elif item.startswith("__links__"):
                links.append(item.removeprefix("__links__"))
            else:
                problems.append(item)

    seen: dict[str, str] = {}
    for a in anchors:
        if a["url"] in seen:
            problems.append(f"重复的锚点 {a['url']}（`{seen[a['url']]}` 和 `{a['title']}`）")
        seen[a["url"]] = a["title"]
        if not a["summary"]:
            problems.append(f"{a['url']}: 这一节没有开头的说明句 — 芝士引用它时没有摘要可给")

    known = set(seen)
    for link in links:
        page = link.split("#")[0].rstrip("/")
        if "#" not in link:  # a link to a whole page, not to one section
            if page.lstrip("/") not in {a["page"] for a in anchors}:
                problems.append(f"文档里链到了不存在的页面：{link}")
            continue
        if link not in known:
            problems.append(f"文档里链到了不存在的锚点：{link}")

    # 图片走 VitePress 的 public/：`/images/x.png` 对应 `public/images/x.png`。
    # 路径写错了页面照常构建、照常发布，只是那张图不显示 —— 又一个「坏掉的样子
    # 和没坏一模一样」，所以在这里查。
    for asset in assets:
        if not (manual_dir / "public" / asset.lstrip("/")).is_file():
            problems.append(f"图片文件不存在：{asset}（应放在 docs/manual/public{asset}）")

    return {"base": SITE_BASE, "anchors": anchors}, problems


def run(manual_dir: Path) -> int:
    payload, problems = build(manual_dir)
    if problems:
        print("FAIL: 用户手册的锚点有问题：")
        for p in problems:
            print(f"  {p}")
        print("::error::manual anchors are not stable/real (see above)")
        return 1
    print(f"PASS: {len(payload['anchors'])} 个锚点都是显式的、唯一的、有摘要的、链得到的")  # type: ignore[arg-type]
    return 0


def self_test() -> int:
    import contextlib
    import io
    import tempfile

    fails: list[str] = []

    def quiet_run(d: Path) -> int:
        """run() prints its own verdict; inside the self-test that is noise."""
        with contextlib.redirect_stdout(io.StringIO()):
            return run(d)

    def case(
        name: str,
        files: dict[str, str],
        should_pass: bool,
        assets: list[str] | None = None,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for fname, content in files.items():
                (d / fname).write_text(content, encoding="utf-8")
            for asset in assets or []:
                target = d / "public" / asset
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"")
            payload, problems = build(d)
            if should_pass and problems:
                fails.append(f"{name}: 本该通过，却报了 {problems}")
            if not should_pass and not problems:
                fails.append(f"{name}: 本该失败，却通过了")
            if should_pass and not problems:
                if quiet_run(d) != 0:
                    fails.append(f"{name}: build() 说合格，run() 却判失败")

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
        "图片路径存在",
        {"a.md": "# A {#x}\n\n一句话。\n\n![界面截图](/images/ok.png)\n"},
        True,
        assets=["images/ok.png"],
    )
    case(
        "图片路径不存在",
        {"a.md": "# A {#x}\n\n一句话。\n\n![界面截图](/images/gone.png)\n"},
        False,
    )
    case("链到整页", {"a.md": "# A {#x}\n\n见 [那边](/a)。\n"}, True)
    case("链到不存在的页面", {"a.md": "# A {#x}\n\n见 [那边](/nope)。\n"}, False)
    case(
        "链到不存在的锚点",
        {"a.md": "# A {#x}\n\n见 [那边](/a#nope)。\n"},
        False,
    )
    case(
        "链到存在的锚点",
        {"a.md": "# A {#x}\n\n见 [那边](/a#y)。\n\n## Y {#y}\n\n一句话。\n"},
        True,
    )
    case("一个页面都没有", {}, False)

    if fails:
        for f in fails:
            print(f"SELF-TEST FAIL: {f}", file=sys.stderr)
        return 1
    print("PASS: check-manual-anchors self-test（缺 slug / 大写 / 重复 / 无摘要 / 死链 / 图片路径 / 空目录）")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test()
    root = Path(__file__).resolve().parents[2]
    return run(root / "docs" / "manual")


if __name__ == "__main__":
    raise SystemExit(main())
