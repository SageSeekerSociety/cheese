#!/usr/bin/env python3
"""Bundle the prototype into ONE self-contained index.html.

    pnpm install && python3 build.py

esbuild bundles src/main.js; the CSS, the script and every image are inlined so
the preview channel only ever fetches a single file.
"""
import base64
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ASSETS = REPO / "frontend/src/assets"


def data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


js = subprocess.run(
    [str(HERE / "node_modules/.bin/esbuild"), "src/main.js", "--bundle", "--format=iife", "--minify", "--target=es2022"],
    cwd=HERE, check=True, capture_output=True, text=True,
).stdout
images = {
    "__IMG_LOGO_SVG__": data_uri(ASSETS / "logo.svg", "image/svg+xml"),
    "__IMG_INVITE__": data_uri(REPO / "docs/manual/public/images/teams-invite.png", "image/png"),
}
html = (HERE / "src/index.html").read_text()
html = html.replace("/*__CSS__*/", (HERE / "src/style.css").read_text())
html = html.replace("/*__JS__*/", js.replace("</script", "<\\/script"))
for key, uri in images.items():
    html = html.replace(key, uri)
(HERE / "index.html").write_text(html)
print(f"index.html {len(html) / 1024:.0f} KB")
