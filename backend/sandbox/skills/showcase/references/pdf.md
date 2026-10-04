# PDF

要把一份结论做成 PDF、又想让它是「知是的一页」，有两条产出路径。**首选路径 1**：页面本身就是排版
（`templates/one-pager.html`、`templates/report.html`），打印出来的 PDF 和房间预览里看到的是同一套
`--cx-*` 设计体系。平台渲染不可用（`cheese check` 报「平台现在渲染不了」）时才走路径 2（typst）。

一份 PDF 落在两处之一：

- **一页纸**：`templates/one-pager.html`，正好一张 A4。汇报、摘要、给会上看的一页结论。
- **长报告**：`templates/report.html`，打印就是多页 A4。篇幅、口径、附录按 `references/report.md`。

两条路径的产物都**必须自己打开看一眼**再交出去（见最后一节）。

## 路径 1（首选）：HTML 模板 → 平台的浏览器打印成 PDF

页面就是成品：写好的 HTML 交给平台的浏览器按打印样式出 A4 PDF，版式和字体与预览一致，背景色保留。

```bash
cheese check one-pager.html --pdf one-pager.pdf                 # 一页纸：页边距 0
cheese check report.html --pdf report.pdf --margin "16mm 18mm"  # 长报告：上下 16mm、左右 18mm
```

- 页边距按页型给：
  - `one-pager.html` 用默认的 `0`，页边距由 `.sheet` 自己的 `padding:14mm` 给，屏幕和打印一致。
  - `report.html` 给 `16mm 18mm`，模板的 `.page` 打印时 `padding:0`，页边距由这里给。
- CSS 里 `@page` 的页边距和纸张大小不生效，以 `--margin` 和 A4 为准。
- 同一条命令也照常出两张截图和报告，打印前顺便看一眼页面。

## 路径 2（平台渲染不可用时的兜底）：typst

按 `documents` 技能的 `documents/references/pdf.md`：用 typst 排一份，中文字体已在 `TYPST_FONT_PATHS` 里。

```bash
typst compile 报告.typ 报告.pdf
typst compile --format png 报告.typ 预览.png   # 缺字体不报错，靠这一眼看出方框
```

typst 产出的是**重排的一份**，版式与 `--cx-*` 设计体系不同——只有平台渲染不可用时才用它，
并在交付时说明「这份是 typst 另排的，不是页面打印出来的那一版」。

## 页面怎么来

一页纸用 `templates/one-pager.html`（槽位与组件见 `references/one-pager.md`），长报告用 `templates/report.html`（见 `references/report.md`）。两份都用 compose 拼出来，样式在模板里：

```bash
python3 scripts/compose.py one-pager content.html one-pager.html --title "预览提速"
```

再按路径 1 用 `cheese check --pdf` 打印。

## 交付前一定要看

PDF 是排版结果的快照，两件事只有渲染出来才知道：**是不是刚好一页**、**中文有没有变成方框**。
`cheese check --pdf` 会打印出页数；再把每页栅格成图看一遍：

```bash
uv run --quiet --with pymupdf python -c "
import pymupdf
d = pymupdf.open('one-pager.pdf')
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
