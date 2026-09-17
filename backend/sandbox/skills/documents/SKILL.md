---
name: documents
description: 需要读取、产出或修改 Word、PowerPoint、Excel、PDF 文件时用。包括：用户上传了这类材料要你看；用户要一份报告、简报、论文、方案、表格或幻灯片；用户在一份已有的文件上要你改内容、还要求格式别动。说明工具在哪、改已有文件为什么不走「打开再另存」、产出之后怎么让用户看见。
---

# 办公文档

平台把 pandoc、typst、uv 和一对中文字体放在这台机器的 `$CHEESE_TOOLCHAIN` 下，并且已经
在 PATH 和 `TYPST_FONT_PATHS` 里。Python 库用 `uv run --with` 按需取用；不需要安装系统包，
也没有权限安装。

放置是在后台进行的，所以**开工前先确认你要用的那个在不在**：

```bash
command -v typst && command -v pandoc
```

不在就是不在——可能还在下载，也可能这台机器的平台拿不到它。**这时候要如实告诉用户这台
机器现在做不了，不要改用别的办法凑一个凑合的结果**：绕道生成的 PDF 和缺字体的 PDF 一样，
看起来正常，问题要等人翻到那一页才发现。

## 三件不同的活

| 要做的 | 用 | 细节 |
|---|---|---|
| 读一份用户给的材料 | Word/PPT/ODT/HTML 用 `pandoc`，PDF 用 `pypdf` | `references/reading.md` |
| **在一份已有的 Word 上改** | `scripts/office.py` | `references/word.md` |
| **在一份已有的 PPT 上改** | `scripts/office.py --plain` | `references/slides.md` |
| 读或改一份表格 | `openpyxl` | `references/sheets.md` |
| 做/读 PDF | `typst` / `pypdf` | `references/pdf.md` |
| 造一份全新的文件 | `python-docx` / `python-pptx` / `openpyxl` / `typst` | 本文件下面 |

参考文件在技能目录的 `references/` 下，和 `scripts/office.py` 在同一个地方（下一节有定位
它们的两行命令）。要动手做哪一类活，先读对应的那一份。

**这三种不能用同一套办法。** 尤其：改已有的文件不要用「打开再另存」——用 python-docx 打开
一份别人排好的文档再保存，样式表、页眉页脚、编号、域、图表、文本框都可能在被读进内存又写
出来的时候丢掉。用户拿到的是「文字对了、排版变了」的文件，而整个过程一行错误都不报。要改
就得改文档里的 XML 本身，那正是 `scripts/office.py` 做的事。

同样地，读完一份材料后**不要重写**它：用户说「改一下第三段」，是让你改那份文件，不是让你
按你的理解另做一份新的给他。

## 在已有的文件上改

脚本在技能目录下。先在 shell 里定位它（每次新开一条命令都要重跑这两行）：

```bash
SKILL=skills/documents
[ -d "$SKILL" ] || SKILL="$CLAUDE_CONFIG_DIR/skills/documents"
uv run --with lxml python3 "$SKILL/scripts/office.py" text 报告.docx
```

六个子命令，常用的三个按这个顺序：

```bash
# 1. 先看原文。段号是后面报错时唯一的坐标
uv run --with lxml python3 "$SKILL/scripts/office.py" text 报告.docx

# 2. 改。默认写成 Word 修订：用户能看出哪句是新加、哪句删了，也能一键拒绝
uv run --with lxml python3 "$SKILL/scripts/office.py" edit 报告.docx -o 改后.docx \
    --replace "旧的说法=新的说法" --author 芝士

# 3. 交付前验一遍：拒绝全部修订，正文能逐字回到原文档吗
uv run --with lxml python3 "$SKILL/scripts/office.py" validate 改后.docx --base 报告.docx
```

另外三个按需用：`revisions` 列出文件里的修订并逐条接受或拒绝（用户说「第二处不要」时走它），
`unpack` / `pack` 在这几个开关不够用时把部件摊开手改。

三条规矩，都是这条路上踩出来的：

- **原文要整句照抄。** 脚本找不到就报错停下，不会猜。找不到通常是因为这句话被拆在几个
  run 或几个段落里，或者你在预览里看到的是渲染后的样子、和文件里的原文不完全一样。先跑
  `text` 把原文复制过来。
- **一处还是多处要说清。** 一段原文出现两次以上，脚本会拒绝执行并列出现位置——这是故意的。
  `--all` 是全改，`--occurrence 2` 是只改第二处（不是前两处）。
- **`validate` 不是走过场。** 它会拒绝掉全部修订再和原文档逐字对比；只有这一步过了，才能
  说「原文没被动过」。改完不验就交付，等于把「有没有悄悄动到别的地方」留给用户去发现。
  但它只管「原文有没有被动过」，不管「改动是不是用户要的那几处」——多改一处同样是格式正确
  的修订，`validate` 照样通过。要核对改了哪几处，跑 `revisions` 看清单。

改完把 `-o` 出来的那份**点名**（见下），别改坏原件、也别改完不告诉用户改了什么。

`--plain` 是不留修订标记直接改。用户明确说「不要修订痕迹」「给我干净的最终版」时才用；
其余情况留修订，让用户自己决定接受还是拒绝。

## 造一份新文件

**用户给的是什么格式，就还什么格式。** 用户传来一份 `.docx` 要你修改，就交回 `.docx`，
不要交一份 Markdown 让他自己转。用户没有指定格式时，按用途选：要打印或存档用 PDF，
要对方接着改用 Word。

| 要产出 | 用 |
|---|---|
| `.docx` | `uv run --with python-docx` |
| `.pptx` | `uv run --with python-pptx` |
| `.xlsx` | `uv run --with openpyxl` |
| `.pdf` | `typst compile 源文件.typ 输出.pdf` |

PDF 走 typst 直接排版，不要绕道「先生成 Word 再转 PDF」。这台机器上没有 LibreOffice；
用户的界面里确实能把 Word 转成 PDF 显示（那是平台另起的服务在做），但那个服务在你的环境
外面，你调不到它，所以「转一下就行」这条路是走不通的。

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

工作区里的文件用户在界面上默认是看不见的。点名之后它直接显示在界面里：Word 和幻灯片
按页翻，表格按单元格看，都不用先下载。做完一份交付物就点一次。

用户可以指着文档里的某一处提要求，你会收到这样一条消息：

```
在 output/预算表.xlsx 的 Sheet1!B7（「1200」）：这个数字应该按季度摊
```

括号里是他当时看到的内容。表格给的是单元格地址，`openpyxl` 直接按这个地址取；文档给的是
页码和原文，先跑 `office.py text` 定位到段，再用这段原文做 `--replace`。改完仍然要
重新点名一次。

## 交付前自己看一眼

这类任务的失败大多不报错。生成命令退出码为 0、文件大小正常、文字提取也正确，但打开是错的。
交付前至少确认三件事：

- **格式**：改的是已有文件时，跑 `office.py validate --base 原文件`，确认拒绝全部修订能回到原文。
- **内容**：把产出的文件读回来（`pandoc 产出.docx -t markdown`），对照原始材料，确认没有编造
  数据、数字能对上。
- **版面**：中文有没有变成空心方框（缺字体时 typst 和 pandoc 都不报错）、纸张对不对、
  页数是不是和要求的差不多。用 `typst compile --format png` 可以直接看一页的样子。

不要把「命令执行成功」当成交付完成。
