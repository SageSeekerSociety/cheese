#!/usr/bin/env bash
# The display face for the home page's headings: 三极行楷简体-粗 by 王荣诚,
# 三极字库 (sjtype.com), marked 免费商用 on its page there
# (https://www.sjtype.com/new_product_show.php?id=174), where the personal and
# company license certificates can also be downloaded. Cut down to the
# characters the home page and the section names actually use, so it costs tens
# of kilobytes rather than megabytes. Re-run after changing those strings.
# Needs uv (for fonttools). The TTF comes from the dengcao/free-fonts mirror,
# which is scriptable; it is the same file as the official download.
set -euo pipefail
cd "$(dirname "$0")/.."
work=node_modules/.display-font
src="$work/SJxingkai-C.ttf"
mkdir -p "$work"
[ -f "$src" ] || curl -fsSL -o "$src" \
  "https://raw.githubusercontent.com/dengcao/free-fonts/main/%E5%85%8D%E8%B4%B9%E5%95%86%E7%94%A8%E5%AD%97%E4%BD%93%EF%BC%88%E5%85%B11328%E6%AC%BE%2C1328%20free%20commercial%20fonts%EF%BC%89/%E4%B8%AD%E6%96%87%E5%AD%97%E4%BD%93%EF%BC%88%E5%85%B1348%E6%AC%BE%EF%BC%8C348%20Chinese%20fonts%EF%BC%89/%E4%B8%89%E6%9E%81%E8%A1%8C%E6%A5%B7%E7%AE%80%E4%BD%93-%E7%B2%97%E5%AD%97%E4%BD%93.ttf"
python3 - > "$work/chars.txt" <<'PY'
import re
# Only the headings set in the display face (class="… display …").
home = open('src/home.mjs', encoding='utf-8').read()
text = ''.join(re.findall(r'class="[^"]*\bdisplay\b[^"]*"[^>]*>(.*?)</h[12]>', home, re.S))
chars = set(re.findall(r'[　-〿㐀-鿿＀-￯]', text))
chars |= set('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz ·?？！，。、：「」')
print(''.join(sorted(chars)))
PY
uvx --from 'fonttools[woff]' pyftsubset "$src" \
  --text-file="$work/chars.txt" --flavor=woff2 --layout-features='*' \
  --output-file=src/fonts/display.woff2
ls -l src/fonts/display.woff2
