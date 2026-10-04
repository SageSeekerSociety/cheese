# PDF

要把一份结论做成 PDF、又想让它是「知是的一页」，有两条产出路径。**首选路径 1**：页面本身就是排版
（`templates/one-pager.html`、`templates/report.html`），打印出来的 PDF 和房间预览里看到的是同一套
`--cx-*` 设计体系。没有浏览器时才走路径 2（typst）。

一份 PDF 落在两处之一：

- **一页纸**：`templates/one-pager.html`，正好一张 A4。汇报、摘要、给会上看的一页结论。
- **长报告**：`templates/report.html`，打印就是多页 A4。篇幅、口径、附录按 `references/report.md`。

两条路径的产物都**必须自己打开看一眼**再交出去（见最后一节）。

## 路径 1（首选）：HTML 模板 → 无头 Chromium 打印成 PDF

页面就是成品：写好的 HTML 用浏览器打印成 PDF，版式与预览一致。平台侧「截图 / 导出」的命令在计划中，
还没有；在那之前用**用户态的无头 Chromium** 自己打印。环境已经备好（见下面「环境」）。

```python
# print.py
from playwright.sync_api import sync_playwright

A4 = {"top": "16mm", "bottom": "16mm", "left": "18mm", "right": "18mm"}

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.goto("file:///var/tmp/sc/gallery/report.html", wait_until="networkidle")
    pg.emulate_media(media="print")      # 让 @media print 的样式生效
    pg.pdf(path="report.pdf", format="A4", print_background=True, margin=A4)
    b.close()
```

```bash
source /var/tmp/pw/env.sh
uv run --quiet --with playwright --python 3.13 python print.py
```

- **`print_background=True` 不能少**：少了底色、状态底纹、图表一起消失。
- **`format="A4"`**，`margin` 按页型给：
  - `report.html`：给 `16mm / 18mm`（模板的 `.page` 在打印时 `padding:0`，页边距由这里给）。
  - `one-pager.html`：四个方向都给 `0`——页边距由 `.sheet` 自己的 `padding:14mm` 给，屏幕和打印一致。
- **`@page { margin }` 会被 playwright 的 `margin` 参数覆盖**。`@page` 里的 `size: A4` 也只是给浏览器
  「直接打印」时用的；用 `page.pdf()` 时以 `format` 为准。别指望在 CSS 里改 `@page` 的页边距能生效。
- `page.pdf()` 只在无头模式可用。

### 环境：缺失的系统库与字体

最小容器里没有 X11 / ATK 这类 Chromium 依赖，也没有 sudo 能装。做法是把这些库和一套中文字体裁成
**用户态前缀**，再用环境变量指过去。已经做好了一份，`source env.sh` 即可：

```bash
# /var/tmp/pw/env.sh
export LD_LIBRARY_PATH=/var/tmp/pw/root/usr/lib/x86_64-linux-gnu:/var/tmp/pw/root/lib/x86_64-linux-gnu
export FONTCONFIG_FILE=/var/tmp/pw/fc/fonts.conf
export PLAYWRIGHT_BROWSERS_PATH=/var/tmp/pw/browsers
export TMPDIR=/var/tmp
```

要重建时：把缺的 `.deb` 解到前缀里（`dpkg-deb -x xxx.deb /var/tmp/pw/root`），装浏览器本体，
再写一个只指向这些目录的 `fonts.conf`：

```bash
uv run --quiet --with playwright playwright install chromium-headless-shell   # 或 chromium
```

**中文会静默变方框。** 字体不由系统 `fontconfig` 提供时，Chromium 找不到 `PingFang SC` /
`Noto Sans CJK` 就落到没有中文字形的字体，中文渲染成一排豆腐块，**不报错**。所以 `FONTCONFIG_FILE`
要指向一个带 `Noto Sans/Serif CJK` 的字体目录（`/var/tmp/pw/fc/fonts.conf` 已经指向
`/var/tmp/pw/root/usr/share/fonts` 里的 Noto CJK）。字体栈本身只用系统栈（见 `design-system.md` §2），
别引外网字体。

## 路径 2（没有浏览器时的兜底）：typst

按 `documents` 技能的 `documents/references/pdf.md`：用 typst 排一份，中文字体已在 `TYPST_FONT_PATHS` 里。

```bash
typst compile 报告.typ 报告.pdf
typst compile --format png 报告.typ 预览.png   # 缺字体不报错，靠这一眼看出方框
```

typst 产出的是**重排的一份**，版式与 `--cx-*` 设计体系不同——只有在真的拿不到浏览器时才用它，
并在交付时说明「这份是 typst 另排的，不是页面打印出来的那一版」。

## 页面怎么来

一页纸用 `templates/one-pager.html`（槽位与组件见 `references/one-pager.md`），长报告用 `templates/report.html`（见 `references/report.md`）。两份都用 compose 拼出来，样式在模板里：

```bash
python3 scripts/compose.py one-pager content.html one-pager.html --title "预览提速"
```

再把拼好的 `one-pager.html` 交给下面的无头 Chromium 打印成 PDF。

## 交付前一定要看

PDF 是排版结果的快照，两件事只有渲染出来才知道：**是不是刚好一页**、**中文有没有变成方框**。
用 pymupdf 把每页栅格成图，逐页看：

```bash
source /var/tmp/pw/env.sh
uv run --quiet --with pymupdf python -c "
import pymupdf
d = pymupdf.open('one-pager.pdf')
print('pages =', d.page_count)                 # 一页纸必须是 1
for i, pg in enumerate(d):
    pg.get_pixmap(dpi=110).save(f'pdf-p{i+1}.png')
"
```

对着每张图确认：

- **一页纸正好 1 页**（`page_count == 1`）。第 2 页哪怕只有两行也是没排好：删内容、缩字号，或调
  `.sheet` 的 `gap` 与字号阶梯。`.sheet` 打印时是 `min-height:297mm` + `box-sizing:border-box`，
  内容超过一页就溢出去；脚注靠 `margin-top:auto` 落到页底。
- **没有裁切**：表格、图、长串数字两侧都没被切掉；窄屏才需要的横滑提示和右缘渐隐在纸面上应已消失
  （两份模板的 `@media print` 里都关掉了）。
- **中文是中文**，不是方框；图内文字渲染后仍 ≥11px。
- 长报告的图表**没有跨页断开**（`break-inside: avoid`），表头每页重出。

看一次，改一轮，不搭反复截图的循环。
