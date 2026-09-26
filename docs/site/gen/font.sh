#!/usr/bin/env bash
# The display face for the home page's headings: Smiley Sans (得意黑, SIL Open
# Font License 1.1, https://github.com/atelier-anchor/smiley-sans), cut down to
# the characters the home page and the section names actually use, so it costs
# tens of kilobytes rather than megabytes. Re-run after changing those strings.
# Needs uv (for fonttools).
set -euo pipefail
cd "$(dirname "$0")/.."
tag=v2.0.1
work=node_modules/.smiley-$tag
mkdir -p "$work"
[ -f "$work/SmileySans-Oblique.ttf" ] || {
  curl -fsSL -o "$work/font.zip" "https://github.com/atelier-anchor/smiley-sans/releases/download/$tag/smiley-sans-$tag.zip"
  python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extract('SmileySans-Oblique.ttf', sys.argv[2])" "$work/font.zip" "$work"
}
python3 - > "$work/chars.txt" <<'PY'
import re
text = open('src/home.mjs', encoding='utf-8').read() + open('src/structure.mjs', encoding='utf-8').read()
chars = set(re.findall(r'[　-〿㐀-鿿＀-￯]', text))
chars |= set('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz ·?？！，。、：「」')
print(''.join(sorted(chars)))
PY
uvx --from 'fonttools[woff]' pyftsubset "$work/SmileySans-Oblique.ttf" \
  --text-file="$work/chars.txt" --flavor=woff2 --layout-features='*' \
  --output-file=src/fonts/smiley-sans-display.woff2
ls -l src/fonts/smiley-sans-display.woff2
