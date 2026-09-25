#!/usr/bin/env python3
"""Fold the slim board-prototype build into ONE self-contained HTML file.

与 `pack-dashboard-prototype.py` / `pack-feedback-prototype.py` 同一套做法，理由也同：
预览通道按秒计费地等人下载，所以产物要**一个文件**、并且要把 @mdi/font 那 403KB
的 woff2 换成只含这个构建真正用到的图标的子集。

两处不同：

1. 本脚本的产物是 `board-prototype.html`。（看板那份的 `OUT` 写成了
   `feedback-prototype.html`，会让两个脚本抢同一个产物名 —— 这里没跟着抄。）
2. 图标是从**构建产物**（html + js）里扫出来的，所以要注意动态拼出来的图标名扫不到。
   这个原型没有 `mdi-${...}` 这种写法，扫得到的就是全部。

跑法（在仓库根）：
    cd frontend && npx vite build --config vite.board-proto.config.mts
    cd .. && python3 pack-board-prototype.py
"""
import base64
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONT = ROOT / "frontend"
SRC = FRONT / "dist-board-proto"
OUT = ROOT / "board-prototype.html"
MDI_CSS = FRONT / "node_modules/@mdi/font/css/materialdesignicons.css"
MDI_WOFF2 = FRONT / "node_modules/@mdi/font/fonts/materialdesignicons-webfont.woff2"
SUBSET = Path(tempfile.gettempdir()) / "mdi-subset-board.woff2"

html = open(f"{SRC}/board-proto.html", encoding="utf-8").read()
css = open(f"{SRC}/proto.css", encoding="utf-8").read()
js = open(f"{SRC}/proto.js", encoding="utf-8").read()

# --- icon subset -----------------------------------------------------------
used = sorted(set(re.findall(r"mdi-[a-z0-9-]+", html + js)))
codepoints = dict(
    re.findall(
        r'\.mdi-([a-z0-9-]+)::before\s*\{\s*content:\s*"\\([0-9A-Fa-f]{4,6})"',
        open(MDI_CSS, encoding="utf-8").read(),
    )
)


def glyph_of(name: str) -> str | None:
    return codepoints.get(name[4:] if name.startswith("mdi-") else name)


missing = [n for n in used if glyph_of(n) is None]
if missing:
    print(f"WARNING: no codepoint for {missing}", file=sys.stderr)
codes = sorted({int(glyph_of(n), 16) for n in used if glyph_of(n)})
print(f"icons referenced by the build: {len(used)} ({len(codes)} codepoints)")

subprocess.run(
    [
        "uv", "run", "--quiet", "--with", "fonttools", "--with", "brotli",
        "pyftsubset", str(MDI_WOFF2),
        "--unicodes=" + ",".join(f"U+{c:04X}" for c in codes),
        "--flavor=woff2",
        "--output-file=" + str(SUBSET),
    ],
    check=True,
)
subset = open(SUBSET, "rb").read()
print(f"mdi woff2 {len(open(MDI_WOFF2, 'rb').read())} -> {len(subset)} bytes")

css = re.sub(
    r'url\(data:font/woff2;base64,[^)]*\)',
    "url(data:font/woff2;base64," + base64.b64encode(subset).decode() + ")",
    css,
    count=1,
)
# 注意这个交替分支的顺序：`font/woff` 是 `font/woff2` 的前缀，不带前瞻的写法会把
# woff2（唯一每个浏览器都用的那个格式）一起删掉，留下空的 src:，于是所有图标渲染成
# 空白。负向前瞻 (?!2) 就是为了保住它。
LEGACY = r"url\(data:(?:application/vnd\.ms-fontobject|font/ttf|font/woff(?!2)|application/x-font-ttf|application/x-font-woff)[^)]*\)\s*(?:format\([^)]*\))?\s*,?\s*"
before = len(css)
css = re.sub(LEGACY, "", css)
css = re.sub(r",\s*;", ";", css)
print(f"css {before} -> {len(css)} (dropped {before - len(css)} of legacy font bytes)")

# --- inline ----------------------------------------------------------------
html = re.sub(r'\s*<link rel="icon"[^>]*>', "", html)
html = re.sub(r'\s*<link rel="apple-touch-icon"[^>]*>', "", html)
html = re.sub(r'\s*<script type="module"[^>]*src="\./proto\.js"[^>]*></script>', "", html)
html = re.sub(r'\s*<link rel="stylesheet"[^>]*href="\./proto\.css"[^>]*>', "", html)
assert "proto.js" not in html and "proto.css" not in html

# 包里若出现字面量 </script> 会提前闭合标签；转义成 <\/script 在它能出现的任何位置
# （字符串、正则、注释）都是合法的。
js = js.replace("</script", "<\\/script")

html = html.replace(
    "</head>", f"<style>\n{css}\n</style>\n<script type=\"module\">\n{js}\n</script>\n</head>"
)
open(OUT, "w", encoding="utf-8").write(html)
print(f"wrote {OUT}: {len(html)} bytes")
