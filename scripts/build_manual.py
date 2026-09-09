#!/usr/bin/env -S uv run --with markdown --script
"""build_manual.py — 把 docs/manual/*.md 编译成 okcheese.com/docs 那个静态站。

页面落在 `<out>/docs/<slug>/index.html`，所以本地起一个最普通的静态服务器，
`/docs/quickstart#connect-repo` 这样的地址就和线上完全一样——文档里的站内链接
不需要为预览改写，也就不会出现「预览时好好的，上线全断」。

线上那台 nginx 要用 `try_files $uri $uri/index.html $uri/ =404`：没有它，
`/docs/quickstart`（不带尾斜杠）只会拿到一个 301，而说明书里发出去的每个链接
都是不带尾斜杠的那种写法。

用法: build_manual.py [输出目录]      默认 docs/manual/.site
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / "docs" / "manual"

CSS = """
:root {
  --ink:#191A1C; --text:#36383C; --muted:#6A6E76; --faint:#9AA0A8;
  --line:#ECEDEF; --line-2:#E2E3E6; --fill:#F4F5F7;
  --canvas:#F7F8FA; --surface:#FFFFFF;
  --accent:#F57F17; --accent-ink:#9A5413;
}
@media (prefers-color-scheme: dark) {
  :root {
    --ink:#F3F4F6; --text:#D3D6DB; --muted:#9CA2AB; --faint:#7A808A;
    --line:#2B2E33; --line-2:#3A3E45; --fill:#212429;
    --canvas:#141517; --surface:#1B1D20;
    --accent:#FFA733; --accent-ink:#FFC670;
  }
}
* { box-sizing: border-box; }
body {
  margin:0; background:var(--canvas); color:var(--text);
  font:16px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
       "Hiragino Sans GB","Microsoft YaHei",sans-serif;
}
.wrap { max-width:1040px; margin:0 auto; display:flex; gap:48px; padding:0 24px; }
nav {
  width:210px; flex:none; padding:40px 0; position:sticky; top:0;
  align-self:flex-start; max-height:100vh; overflow:auto;
}
nav .brand { font-weight:700; color:var(--ink); font-size:15px; margin-bottom:20px; display:block;
             text-decoration:none; }
nav a { display:block; color:var(--muted); text-decoration:none; padding:5px 0; font-size:14px; }
nav a:hover { color:var(--ink); }
nav a.on { color:var(--accent-ink); font-weight:600; }
nav .dl { margin-top:16px; padding-top:14px; border-top:1px solid var(--line);
          color:var(--accent-ink); font-size:13px; }
nav .sub { padding-left:12px; font-size:13px; border-left:1px solid var(--line); margin-left:2px; }
main {
  flex:1; min-width:0; background:var(--surface); border:1px solid var(--line);
  border-radius:12px; padding:40px 48px; margin:32px 0;
}
h1 { color:var(--ink); font-size:30px; line-height:1.35; margin:0 0 20px; }
h2 { color:var(--ink); font-size:21px; margin:40px 0 12px; padding-top:8px;
     border-top:1px solid var(--line); }
h1+h2 { border-top:none; margin-top:24px; }
h3 { color:var(--ink); font-size:17px; margin:28px 0 8px; }
a { color:var(--accent-ink); }
code { background:var(--fill); padding:1px 5px; border-radius:4px; font-size:0.9em;
       font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
pre { background:var(--fill); padding:14px 16px; border-radius:8px; overflow:auto; }
pre code { background:none; padding:0; }
blockquote { margin:16px 0; padding:2px 0 2px 16px; border-left:3px solid var(--line-2);
             color:var(--muted); }
table { border-collapse:collapse; width:100%; margin:16px 0; font-size:15px; }
th,td { border:1px solid var(--line); padding:8px 12px; text-align:left; }
th { background:var(--fill); color:var(--ink); }
strong { color:var(--ink); }
hr { border:none; border-top:1px solid var(--line); margin:32px 0; }
h1 .anchor, h2 .anchor, h3 .anchor {
  opacity:0; margin-left:8px; font-weight:400; text-decoration:none; font-size:0.7em;
}
h1:hover .anchor, h2:hover .anchor, h3:hover .anchor { opacity:0.45; }
@media (max-width: 800px) {
  .wrap { flex-direction:column; gap:0; }
  nav { width:auto; position:static; max-height:none; padding:24px 0 0; }
  main { padding:28px 22px; }
}
"""

FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)
HEADING = re.compile(r"^(#{1,3})\s+(.*?)\s*\{#([a-z0-9-]+)\}\s*$", re.M)


def page_title(body: str, fallback: str) -> str:
    m = FRONT.match(body)
    if m:
        for line in m.group(1).splitlines():
            if line.startswith("title:"):
                return line.split(":", 1)[1].strip()
    return fallback


def build(out: Path) -> int:
    pages = sorted(p for p in MANUAL.glob("*.md") if p.name != "README.md")
    if not pages:
        print("FAIL: docs/manual 下没有页面", file=sys.stderr)
        return 1
    order = ["quickstart", "concepts", "working-with-cheese"]
    pages.sort(key=lambda p: (order.index(p.stem) if p.stem in order else 99, p.stem))

    if out.exists():
        shutil.rmtree(out)

    metas = []
    for path in pages:
        raw = path.read_text(encoding="utf-8")
        body = FRONT.sub("", raw)
        metas.append(
            (
                path.stem,
                page_title(raw, path.stem),
                body,
                [(m.group(1), m.group(2), m.group(3)) for m in HEADING.finditer(body)],
            )
        )

    md = markdown.Markdown(extensions=["attr_list", "tables", "fenced_code", "sane_lists"])

    for slug, title, body, heads in metas:
        nav = ['<a class="brand" href="/docs/quickstart">知是 · 使用说明</a>']
        for s2, t2, _, h2 in metas:
            nav.append(f'<a class="{"on" if s2 == slug else ""}" href="/docs/{s2}">{t2}</a>')
            if s2 == slug:
                subs = [h for h in h2 if h[0] == "##"]
                if subs:
                    nav.append('<div class="sub">')
                    nav += [f'<a href="#{a}">{t}</a>' for _, t, a in subs]
                    nav.append("</div>")
        # 平台上没有"把这一份带走"的入口——工作区文件只能一个一个下载，git 服务
        # 又只认沙箱令牌。所以站点自己带一个：在预览的独立页面里点它就是下载。
        nav.append('<a class="dl" href="/manual.zip" download>⤓ 下载全部（Markdown）</a>')
        md.reset()
        html = md.convert(body)
        # 每个标题挂一个可复制的 # —— 说明书的地址是拿来发给人的。
        html = re.sub(
            r'<(h[123]) id="([a-z0-9-]+)">(.*?)</\1>',
            r'<\1 id="\2">\3<a class="anchor" href="#\2">#</a></\1>',
            html,
        )
        target = out / "docs" / slug / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
            '<meta name=viewport content="width=device-width,initial-scale=1">'
            f"<title>{title} · 知是</title><style>{CSS}</style></head><body>"
            f'<div class=wrap><nav>{"".join(nav)}</nav><main>{html}</main></div>'
            "</body></html>",
            encoding="utf-8",
        )

    with zipfile.ZipFile(out / "manual.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for md_file in sorted(MANUAL.glob("*.md")):
            z.write(md_file, f"知是说明书/{md_file.name}")
        z.write(MANUAL / "anchors.json", "知是说明书/anchors.json")

    (out / "index.html").write_text(
        '<!doctype html><meta charset=utf-8><meta http-equiv=refresh content="0;url=/docs/quickstart">',
        encoding="utf-8",
    )
    anchors = json.loads((MANUAL / "anchors.json").read_text(encoding="utf-8"))
    print(f"built {len(metas)} pages / {len(anchors['anchors'])} anchors → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(build(Path(sys.argv[1]) if len(sys.argv) > 1 else MANUAL / ".site"))
