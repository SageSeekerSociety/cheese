"""Make the sign-in scene texture that Paper Shaders preprocesses.

The heat scene reads a logo that the library's own `toProcessedHeatmap` has
already worked over. That runs in a browser, so this serves the mark and the installed library on
localhost and runs them in headless Chromium. Run after build_brand.py:

    uv run --with fonttools --with skia-pathops --with uharfbuzz \
        --with py7zr --with pillow python scripts/brand/scene_textures.py

It needs frontend/node_modules installed and a Chromium on PATH (or CHROME=...).
"""

import base64
import functools
import http.server
import io
import os
import re
import subprocess
import tempfile
import threading

from PIL import Image

import build_brand as B

SHADERS = os.path.join(B.ROOT, 'frontend', 'node_modules', '@paper-design', 'shaders', 'dist')
OUT = os.path.join(B.ASSETS, 'brand-scene')

PAGE = """<!doctype html><meta charset="utf-8"><body>
<script type="module">
import { toProcessedHeatmap } from './shaders/index.js'
const url = (b) => new Promise((r) => { const f = new FileReader(); f.onload = () => r(f.result); f.readAsDataURL(b) })
const put = (id, v) => { const p = document.createElement('pre'); p.id = id; p.textContent = v; document.body.append(p) }
try {
  put('heat', await url((await toProcessedHeatmap('./heat-in.png')).blob))
} catch (e) { put('error', String(e)) }
</script>
"""


def mark_png(size, colour, pad):
    """The mark alone on a clear ground, as build_brand draws it."""
    logo = B.mark()
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    k = (size - 2 * pad) / 1000
    img.paste(Image.new('RGBA', (size, size), B.rgb(colour) + (255,)), (0, 0), B.coverage(logo, size, k, pad, pad))
    return img


def main():
    with tempfile.TemporaryDirectory(dir=os.path.expanduser('~')) as tmp:
        os.symlink(SHADERS, os.path.join(tmp, 'shaders'))
        # the heatmap processor paints onto white, so it is fed a dark mark
        mark_png(1000, B.TILE, 0).save(os.path.join(tmp, 'heat-in.png'))
        open(os.path.join(tmp, 'index.html'), 'w').write(PAGE)
        quiet = type('Quiet', (http.server.SimpleHTTPRequestHandler,), {'log_message': lambda *a: None})
        handler = functools.partial(quiet, directory=tmp)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            dom = subprocess.run(
                [os.environ.get('CHROME', 'chromium'), '--headless=new', '--disable-gpu', '--virtual-time-budget=60000',
                 '--dump-dom', f'http://127.0.0.1:{server.server_port}/index.html'],
                capture_output=True, text=True, timeout=300).stdout
        finally:
            server.shutdown()
    found = dict(re.findall(r'<pre id="(\w+)">([^<]*)</pre>', dom))
    if 'error' in found or 'heat' not in found:
        raise SystemExit(f"processing failed: {found.get('error', 'no output')}")
    # the heat output is a soft glow 1750px across; 1024 keeps the file small
    path = os.path.join(OUT, 'logo-heat.png')
    img = Image.open(io.BytesIO(base64.b64decode(found['heat'].split(',', 1)[1])))
    img.resize((1024, 1024), Image.LANCZOS).save(path, optimize=True)
    print(os.path.relpath(path, B.ROOT), (1024, 1024))


if __name__ == '__main__':
    main()
