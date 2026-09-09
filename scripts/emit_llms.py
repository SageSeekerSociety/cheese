#!/usr/bin/env python3
"""emit_llms.py — 给模型的那一份：llms.txt + 每页的 .md 双胞胎。

VitePress 负责给人看的那一半（HTML、导航、搜索）。这个脚本补上另一半，因为
那五家真正在做文档的站都提供它——2026-09-09 逐个实测：code.claude.com/docs、
developers.openai.com、docs.stripe.com、docs.linear.app、vercel.com/docs 全部
发布 llms.txt，并且每一页都有一个 .md 原文。

约定是：`- [标题](绝对URL.md): 一句话摘要`。摘要取自每节的第一句话，规则和
守卫（.claude/scripts/check-manual-anchors.py）用的是同一份实现——两处各写一遍
就会出现「守卫说合格、llms.txt 里却是空的」。

每份 .md 开头指回 llms.txt，抄的是 code.claude.com：模型从任何一页进来，都该
知道完整目录在哪。

用法: emit_llms.py <vitepress 的 dist 目录>
"""

from __future__ import annotations

import importlib.util
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / "docs" / "manual"
SITE = "https://okcheese.com"
BASE = f"{SITE}/docs"
# 侧边栏的分组顺序 —— 与 docs/manual/.vitepress/config.mts 里的 GROUPS 一致。
GROUPS = ["开始", "基础", "干活", "产出", "资源"]


def _anchor_rules():
    """守卫模块本身（文件名带连字符，只能按路径加载）。"""
    spec = importlib.util.spec_from_file_location(
        "manual_anchors", ROOT / ".claude" / "scripts" / "check-manual-anchors.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def emit(dist: Path) -> int:
    if not dist.is_dir():
        print(f"FAIL: {dist} 不存在 —— 先跑 vitepress build", file=sys.stderr)
        return 1

    payload, problems = _anchor_rules().build(MANUAL)
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1
    anchors = payload["anchors"]

    lead = (
        "> ## Documentation Index\n"
        f"> Fetch the complete documentation index at: {BASE}/llms.txt\n"
        "> Use this file to discover all available pages before exploring further.\n\n"
    )

    def field(raw: str, key: str) -> str | None:
        for line in raw.splitlines()[:12]:
            if line.startswith(f"{key}:"):
                return line.split(":", 1)[1].strip()
        return None

    # index.md 是首页（vitepress 的 home 布局，没有正文），README.md 是写给
    # 维护者的规矩——两个都不是说明书的一页，不该出现在给模型的目录里。
    sources = [p for p in MANUAL.glob("*.md") if p.name not in {"README.md", "index.md"}]
    # 顺序跟侧边栏一致：读者看到的次序和模型拿到的次序不该是两回事。
    sources.sort(key=lambda p: (int(field(p.read_text("utf-8"), "order") or 999), p.name))

    pages: list[tuple[str, str, str, str]] = []  # (slug, title, summary, group)
    for path in sources:
        raw = path.read_text(encoding="utf-8")
        body = raw.split("---\n", 2)[2] if raw.startswith("---\n") else raw
        # 站内链接在源码里是 base 之下的路径（`/concepts#task`）。给模型的这份要
        # 独立成立——它是被单独抓走的，身边没有 base 可依，所以写成绝对地址。
        body = re.sub(r"\]\((/[^)\s]*)\)", lambda m: f"]({BASE}{m.group(1)})", body)
        (dist / f"{path.stem}.md").write_text(lead + body.lstrip(), encoding="utf-8")
        title = field(raw, "title") or path.stem
        summary = next(
            (a["summary"] for a in anchors if a["page"] == path.stem and a["summary"]), ""
        )
        pages.append((path.stem, title, summary, field(raw, "group") or "其他"))

    # 分组和顺序跟侧边栏一模一样 —— 人看到的目录和模型拿到的目录不该是两回事,
    # 否则「文档里第三部分那页」这种话在两边指的不是同一页。
    lines = [
        "# 知是 · 使用说明",
        "",
        "> 知是是一个你和 AI 队友一起做项目的地方。这份文档讲怎么用它：从建第一个",
        "> 项目，到把 AI 干出来的活合并进主分支。",
        "",
    ]
    seen: list[str] = []
    for _, _, _, group in pages:
        if group not in seen:
            seen.append(group)
    order = [g for g in GROUPS if g in seen] + [g for g in seen if g not in GROUPS]
    for group in order:
        lines += [f"## {group}", ""]
        lines += [
            f"- [{title}]({BASE}/{slug}.md): {summary}"
            for slug, title, summary, g in pages
            if g == group
        ]
        lines.append("")

    (dist / "llms.txt").write_text("\n".join(lines), encoding="utf-8")

    # 平台上没有「把这一份带走」的入口：工作区文件只能一个一个下载，git 服务
    # 又只认沙箱令牌。所以站点自己带一个。
    with zipfile.ZipFile(dist / "manual.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for md_file in sorted(MANUAL.glob("*.md")):
            z.write(md_file, f"知是说明书/{md_file.name}")

    print(f"emitted llms.txt + {len(pages)} 个 .md + manual.zip → {dist}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(emit(Path(sys.argv[1]).resolve()))
