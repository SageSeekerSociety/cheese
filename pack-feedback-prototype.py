#!/usr/bin/env python3
"""Fold the slim feedback-prototype build into ONE self-contained HTML file.

Two size decisions, both driven by what the preview channel costs:

1. One file. The topic preview serves the selected artifact's own directory, so
   a multi-file build would mean extra uploads (and extra artifact cards in the
   room). One file keeps the preview to a single request.

2. A subset icon font. @mdi/font is 403KB of woff2 for ~7500 icons; this build
   references 64 of them, and inlined as base64 it would be the single biggest
   thing in the file — on a 130KB/s link, most of the wait. The subset is a few
   KB. The legacy eot/ttf/woff faces are dropped for the same reason (every
   browser this preview opens in takes the woff2).

The icon set is derived from the strings actually present in the built JS+HTML,
NOT from the stylesheet: the stylesheet lists every icon in the package, which
would subset nothing. A dynamic `mdi-${...}` would be invisible to this, so a
packed page has to be eyeballed once: every icon on every page must paint a
glyph. (How this build was checked: render each `.v-icon`'s `::before` code
point on a canvas under "Material Design Icons" and again under a family that
does not exist — identical bitmaps mean it fell through to a fallback font,
which is what the missing-glyph box looks like.)
"""
import base64
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# 路径都从脚本自己的位置推：这份脚本住在仓库根，机位换了、worktree 换了都不用改。
# 跑法（在仓库根）：先
#     cd frontend && npx vite build --config vite.feedback-proto.config.mts
# 再
#     python3 pack-feedback-prototype.py
# 产物是一个自包含的 HTML，直接把它当话题预览的首页服务即可（它不需要同级文件）。
ROOT = Path(__file__).resolve().parent
FRONT = ROOT / "frontend"
SRC = FRONT / "dist-feedback-proto"
OUT = ROOT / "feedback-prototype.html"
MDI_CSS = FRONT / "node_modules/@mdi/font/css/materialdesignicons.css"
MDI_WOFF2 = FRONT / "node_modules/@mdi/font/fonts/materialdesignicons-webfont.woff2"
SUBSET = Path(tempfile.gettempdir()) / "mdi-subset.woff2"

html = open(f"{SRC}/feedback-proto.html", encoding="utf-8").read()
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
# The stylesheet's selectors carry the `mdi-` prefix (`.mdi-magnify::before`) but
# the captured name is the bare icon name, so strip it before looking up.
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

# Swap the whole face in, then drop the other formats outright: they are only
# reachable if woff2 fails, and base64 makes them 6.5MB of the stylesheet.
css = re.sub(
    r'url\(data:font/woff2;base64,[^)]*\)',
    "url(data:font/woff2;base64," + base64.b64encode(subset).decode() + ")",
    css,
    count=1,
)
# Careful with the alternation: `font/woff` is a PREFIX of `font/woff2`, and an
# unanchored alternative matches it — that silently deleted the woff2 (the one
# format every browser actually uses) and left the icon font with an empty
# `src:`, which renders every mdi icon as nothing. The lookahead keeps woff2.
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

# A literal </script> inside the bundle would end the tag early; escaping it as
# <\/script is valid wherever it can legally appear (string, regex, comment).
js = js.replace("</script", "<\\/script")

html = html.replace(
    "</head>", f"<style>\n{css}\n</style>\n<script type=\"module\">\n{js}\n</script>\n</head>"
)
open(OUT, "w", encoding="utf-8").write(html)
print(f"wrote {OUT}: {len(html)} bytes")
