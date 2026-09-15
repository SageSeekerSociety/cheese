---
name: documents
description: 需要读取或产出 Word、PowerPoint、Excel、PDF 文件时用。包括：用户上传了这类材料要你看；用户要一份报告、简报、论文、方案、表格或幻灯片；要把已有内容导出成可以下载的文件。说明工具在哪、默认值里哪些是错的、产出之后怎么让用户看见。
---

# 办公文档

这个环境里已经装好了 pandoc、typst 和中文字体，Python 库用 `uv run --with` 按需取用。
不需要安装系统包，也没有权限安装。

## 读用户给的文件：用 pandoc，不要自己解析

```bash
pandoc 材料.docx -t markdown
```

Word、PowerPoint、ODT、EPUB、HTML 都可以这样转成 Markdown 再读。

不要用正则或 XML 解析去抽 `.docx` 里的文字。这条不是风格建议：`.docx` 里空单元格是自闭合的
`<w:t/>`，正则匹配下一个 `</w:t>` 时会跨过若干单元格，把中间的标签当成正文抓出来。产出的内容
看起来像正常文本，错误要到用户读报告时才发现。

Excel 用 `openpyxl` 读，pandoc 不处理 `.xlsx`。

## 产出文件：先定格式，再选工具

**用户给的是什么格式，就还什么格式。** 用户传来一份 `.docx` 要你修改，就交回 `.docx`，
不要交一份 Markdown 让他自己转。用户没有指定格式时，按用途选：要打印或存档用 PDF，
要对方接着改用 Word。

| 要产出 | 用 |
|---|---|
| `.docx` | `uv run --with python-docx` |
| `.pptx` | `uv run --with python-pptx` |
| `.xlsx` | `uv run --with openpyxl` |
| `.pdf` | `typst compile 源文件.typ 输出.pdf` |

PDF 走 typst 直接排版，不要绕道「先生成 Word 再转 PDF」——这个环境里没有能做这种转换的工具，
LibreOffice 和 Chromium 都不在，也装不上。

### python-docx 的默认纸张是 US Letter，必须改成 A4

不改的话产出的是 8.5×11 英寸，打印和排版都不对，而文件本身没有任何异常，
生成过程也不会报错。

```python
from docx.shared import Mm
for s in doc.sections:
    s.page_width, s.page_height = Mm(210), Mm(297)
```

## 产出之后要点名，否则界面上看不到

用 `cheese_artifact` 点名工作区里的那份文件，例如 `path="output/评审简报.docx"`。

工作区里的文件用户在界面上默认是看不见的。点名之后它出现在成果里，用户可以打开和下载。
做完一份交付物就点一次。

## 交付前自己看一眼

这类任务的失败大多不报错。生成命令退出码为 0、文件大小正常、文字提取也正确，
但打开是错的。交付前至少确认两件事：

- **内容**：把产出的文件读回来（`pandoc 产出.docx -t markdown`），对照原始材料，
  确认没有编造数据、数字能对上。
- **版面**：中文有没有变成空心方框（缺字体时 typst 和 pandoc 都不报错）、纸张对不对、
  页数是不是和要求的差不多。用 `typst compile --format png` 可以直接看一页的样子。

不要把「命令执行成功」当成交付完成。
