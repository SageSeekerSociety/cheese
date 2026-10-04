#!/usr/bin/env python3
# ruff: noqa: E501  (usage docstrings show JSON and command lines that read worse wrapped)
"""把一段内容片段拼进模板，直接产出可以 `cheese show` 的完整 HTML。

为什么要有它：一份页面模板里，`<head>`（全部 CSS 与 `--cx-*` token）和末尾的脚本
（目录重建、幻灯片翻页、表格渲染器……）每次都是同一份，几百行。让 agent 读一遍再把
整份重写出来，是把输出 token 花在没变的部分上。这里改成：模板固定，agent 只写
「内容片段」——`<!-- CONTENT:START -->` 与 `<!-- CONTENT:END -->` 之间那一块，也就是
agent 真正要动的那部分；头里的样式和尾里的脚本由这个脚本原样保留。

用法：
    python3 compose.py <type> <content.html> <out.html> [--title "页面名字"]

  <type>         report / dashboard / data-table / compare / explainer / plan / slides / one-pager
  <content.html> agent 写好的内容片段（slides 是若干 <section class="slide"> 依次排列）
  <out.html>     产出的完整页面
  --title        写进 <head> 的 <title>。不给就沿用模板里的标题（模板标题是占位，
                 生产环境下会被下面的自检拦下来——请总是给一个真实名字）。

纯标准库。拼完会自检：还有没有 `SLOT` 标记、标题或正文里还有没有占位文字；
有就报错退出（退出码 1），不产出一个半成品。

`--allow-placeholders` 关掉自检，只给模板自身的往返验证用，正常写页面不要用它。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE.parent / "templates"

TYPES = (
    "report",
    "dashboard",
    "data-table",
    "compare",
    "explainer",
    "plan",
    "slides",
    "one-pager",
)

START = "<!-- CONTENT:START -->"
END = "<!-- CONTENT:END -->"

# 模板里 <title> 的默认值，都是占位——没换成真实名字就报错。
TITLE_PLACEHOLDERS = {
    "报告",
    "看板",
    "表格",
    "数据表格",
    "幻灯片",
    "方案名称",
    "计划",
    "A4 单页",
    "单页标题",
    "它怎么运作",
    "页面标题",
}

# 正文里出现这些字样，说明示例/占位文字没换掉。只列能一眼认出是模板示例的字符串，
# 不列「报告」「计划」这类正常的词，免得把真实内容误判。
BODY_PLACEHOLDERS = (
    "示例行",
    "示例图",
    "示例图表",
    "示例条状图",
    "示例横条图",
    "占位一行",
    "占位图",
    "文档标题",
    "看板标题",
    "表格标题",
    "演示标题",
    "计划标题",
    "一句话说明这份文档得出的结论。",
    "一句话说明这张表是什么。",
    "一句话说清这份计划要做什么。",
    "一句话说清这份说明讲的是什么。",
    "把这一页的结论用一句话写在这里，带具体数字。",
    "这一段交代背景：这件事从哪来、现在卡在哪。",
    "这一段说明做法：整体路径是什么、为什么这样选。",
    "这一步发生的事",
    "第二步发生的事",
    "第一阶段的名字",
    "第二阶段的名字",
    "第一条要点，一句话，带真实数字。",
    "第一项",
    "第一行",
    "第一件事",
    "要定的事",
    "谁推进",
    "数据来源与快照时间。",
    "数据来源与生成时间。",
)

_TAG = re.compile(r"<[^>]+>")
_COMMENT = re.compile(r"<!--.*?-->", re.S)


class ComposeError(Exception):
    pass


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ComposeError(f"找不到文件：{path}") from None


def _set_title(html: str, title: str) -> str:
    escaped = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    new, n = re.subn(
        r"<title>.*?</title>", f"<title>{escaped}</title>", html, count=1, flags=re.S
    )
    if n == 0:
        raise ComposeError("模板里没有 <title> 标签")
    return new


def _title_text(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, flags=re.S)
    return _COMMENT.sub("", m.group(1)).strip() if m else ""


def _visible_text(html: str) -> str:
    """粗略取出可读文字：先去注释（含 SLOT 注释），再去标签。"""
    return _TAG.sub(" ", _COMMENT.sub(" ", html))


def _slide_title(block: str, fallback: str) -> str:
    """从一张幻灯片里取一个短标题，给 deck-index 当目录用。"""
    for pattern in (
        r"<h1[^>]*>(.*?)</h1>",
        r"<h2[^>]*>(.*?)</h2>",
        r"<p[^>]*class=\"[^\"]*slide__kicker[^\"]*\"[^>]*>(.*?)</p>",
    ):
        m = re.search(pattern, block, flags=re.S)
        if m:
            text = _COMMENT.sub("", m.group(1))
            text = _TAG.sub("", text).strip()
            if text:
                return text
    return fallback


def _rebuild_deck_index(html: str, fragment: str, title: str) -> str:
    """按实际写出的 section 重建 <script id="deck-index">：标题与各页清单。

    片段只写 section，这张目录表在片段之外——这里从 section 反推出来，
    免得它和实际幻灯片对不上。老幻灯片里没有 <h1>/<h2> 的用 kicker，再退到 id。
    """
    m = re.search(
        r'(<script type="application/json" id="deck-index">)(.*?)(</script>)',
        html,
        flags=re.S,
    )
    if not m:
        return html
    src = _COMMENT.sub("", fragment)  # 注释里也可能出现「<section」，先去掉
    hits = list(re.finditer(r'<section\b[^>]*class="[^"]*\bslide\b[^"]*"[^>]*>', src))
    slides: list[dict[str, str]] = []
    for i, hit in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(src)
        block = src[hit.start() : end]
        idm = re.search(r'id="([^"]+)"', hit.group(0))
        sid = idm.group(1) if idm else f"s{i + 1}"
        slides.append({"id": sid, "title": _slide_title(block, sid)})
    if not slides:
        return html
    payload = json.dumps({"title": title, "slides": slides}, ensure_ascii=False)
    return (
        html[: m.start()]
        + m.group(1)
        + "\n"
        + payload
        + "\n"
        + m.group(3)
        + html[m.end() :]
    )


def compose(type_: str, content: str, template: str, title: str | None) -> str:
    if START not in template or END not in template:
        raise ComposeError(f"模板 {type_}.html 里没有 {START} / {END} 标记")

    head, rest = template.split(START, 1)
    _old, tail = rest.split(END, 1)

    if not content.strip():
        raise ComposeError("内容片段是空的")

    # 片段原样放回，只在两侧各保一个换行，方便阅读；模板自身的空白因此能原样往返。
    fragment = content
    if not fragment.startswith("\n"):
        fragment = "\n" + fragment
    if not fragment.endswith("\n"):
        fragment = fragment + "\n"

    out = f"{head}{START}{fragment}{END}{tail}"

    if title is not None:
        out = _set_title(out, title)
        if type_ == "slides":
            out = _rebuild_deck_index(out, fragment, title)

    return out


def check(out: str, *, sheet: str) -> None:
    """自检：还有 SLOT 标记或占位文字就报错。"""
    problems: list[str] = []

    if "SLOT" in out:
        n = out.count("SLOT")
        problems.append(f"还有 {n} 处 SLOT 标记没换掉（搜 “SLOT”）")

    title = _title_text(out)
    if title in TITLE_PLACEHOLDERS or not title:
        problems.append(f"<title> 还是占位“{title}”，用 --title 给一个真实名字")

    text = _visible_text(out)
    hit = [p for p in BODY_PLACEHOLDERS if p in text]
    if hit:
        problems.append("正文里还有模板占位文字：" + "、".join(hit))

    if problems:
        raise ComposeError("\n  - ".join(["自检没过：", *problems]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="把内容片段拼进 showcase 模板，产出完整 HTML。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("type", help="模板类型：" + " / ".join(TYPES))
    ap.add_argument("content", help="内容片段文件路径")
    ap.add_argument("out", help="产出的 HTML 路径")
    ap.add_argument("--title", help="写进 <title> 的页面名字")
    ap.add_argument(
        "--allow-placeholders",
        action="store_true",
        help="跳过错位自检（只给模板往返验证用）",
    )
    args = ap.parse_args(argv)

    try:
        if args.type not in TYPES:
            raise ComposeError(f"未知类型“{args.type}”，可选：{' / '.join(TYPES)}")
        template_path = TEMPLATES / f"{args.type}.html"
        template = _read(template_path)
        content = _read(Path(args.content))
        out = compose(args.type, content, template, args.title)
        if not args.allow_placeholders:
            check(out, sheet=args.type)
    except ComposeError as e:
        print(f"compose.py: {e}", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out, encoding="utf-8")

    total = out.count("\n") + 1
    print(
        f"compose.py: {args.type} + 内容片段 → {out_path} "
        f"（{len(out.encode('utf-8'))} 字节，{total} 行）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
