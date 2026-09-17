# 读用户给的材料

## Word、PowerPoint、ODT、EPUB、HTML 用 pandoc

```bash
pandoc 材料.docx -t markdown
```

一次读完，格式也一并转成 Markdown（标题层级、粗体、列表、表格都保留）。材料长的时候先
`pandoc 材料.docx -t markdown | head -100` 看开头，再决定读哪一段。

**不要用正则或 XML 解析去抽 `.docx` 里的文字。** 这条不是风格建议：`.docx` 里空单元格是
自闭合的 `<w:t/>`，正则匹配下一个 `</w:t>` 时会跨过若干单元格，把中间的标签当成正文抓出来。
产出的内容看起来像正常文本，错误要到用户读报告时才发现。

## Excel 用 openpyxl

pandoc 不处理 `.xlsx`：

```bash
uv run --with openpyxl python3 -c "
from openpyxl import load_workbook
wb = load_workbook('预算表.xlsx', data_only=True)
ws = wb.active
for row in ws.iter_rows(min_row=1, max_row=10, values_only=True):
    print(row)
"
```

`data_only=True` 读的是上次保存时缓存下来的计算结果。**如果这个文件从来没被 Excel 打开过
（是程序生成的），缓存里没有值，读出来会是 `None`**——这不是文件坏了。先 `cheese recalc`
把公式算出来再读（见 `sheets.md`）；不重算就去掉 `data_only` 的话，看到的是公式本身，
那同样是有用的信息，但别把它当成算好的数字念给用户。

## PDF 用 pypdf

```bash
uv run --with pypdf python3 -c "
from pypdf import PdfReader
for page in PdfReader('论文.pdf').pages:
    print(page.extract_text())
"
```

扫描件（整页是图片）抽不出文字，抽出来是空的，这时候要如实说「这份 PDF 是扫描件，我读不到
里面的文字」，不要凭空编内容。

## 读完要分清「读懂」和「定位」

用 pandoc 读完一份材料，你知道了它说了什么；但要改它的时候，改的是**文件里的原文**，不是
pandoc 转出来的 Markdown。两者常常不一样——pandoc 会把 Markdown 的 `**粗体**` 去掉、把
表格转成管道表格、把连续空格合并。做修改时先跑 `office.py text 原文件`，照那份输出里的
文字来定位，不要照你自己转出来的 Markdown。

## 2007 以前的老格式：先升级，再改

`.doc` / `.ppt` / `.xls` 不是 zip，是另一套二进制格式，这台机器读不了也写不了，
`office.py` 和 `openpyxl` 对它们都无效。

**要改就先升级格式**：

```bash
cheese convert 旧稿.doc --to docx      # .ppt → pptx，.xls → xlsx 同理
```

转换在平台那边做（那里有 LibreOffice）。原文件不动，转出来的是旁边一份新文件。

**升级完必须明确告诉用户这件事。** 转出来的文件和原件不是同一个东西——版式可能有细微
差别，宏和某些旧特性不会跟过来。他后面要发给别人的是哪一份，是他的决定，不是你的。

只要读不要改的时候，`.doc` 和 `.ppt` 可以先用 pandoc 直接试，能不能读取决于文件本身；
`.xls` 读不了（见 `sheets.md`），要读也先 `cheese convert --to xlsx`。读不出来就如实说
读不出来，不要用别的办法猜里面的内容。
