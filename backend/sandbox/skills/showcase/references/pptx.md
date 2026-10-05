# 幻灯片（.pptx）

## 什么时候做 .pptx

**只在用户点名要那份文件本身时才做**（「给我一份 pptx」「要能编辑的幻灯片」「发给别人，他们要用 PowerPoint 改」）。

只是要摆出来给人看，用**单文件 HTML**：CC 的幻灯片结构是**一张幻灯片一个 `<section id="…">`**，讲者备注写成该 section 里的**最后一个 `<aside>`**，再加一份 **deck index**（标题、顺序、分节）放在开头。预览里约 1 秒就出来，还能圈选提问，改一次就换一版。HTML 版才是默认，.pptx 是有明确需求时的一条并行产出。

`.pptx` 是二进制，**不能作为技能文件交付**。技能里能交付的是脚本、说明和一份 JSON 稿：`scripts/build_deck.py`、本文件、`scripts/deck-spec.example.json`（示例稿）。要交给人，交 `.pptx` 本身走 `cheese` 的交付（递卡），不塞进技能目录。

## 怎么跑

```sh
uv run --with python-pptx python scripts/build_deck.py spec.json out.pptx
```

一份 16:9 的 .pptx：`prs.slide_width = Inches(13.333)`、`prs.slide_height = Inches(7.5)`（python-pptx 默认是 4:3，16:9 必须显式设，而且所有形状用绝对英寸盒子摆放，不用版式占位符）。

## 稿子（JSON）结构

顶层：`title`（页脚左端与文件名）、`subtitle`、`meta`、`slides[]`。每张幻灯片一个对象，`layout` 九个之一，其余字段按版式取。所有字段都可缺省，缺了就不画；每张都接受 `notes`（写进讲者备注）。

| layout | 字段 | 用来放 |
|---|---|---|
| `cover` | `eyebrow` `title` `subtitle` `hero_from` `hero_to` `hero_cap` `meta` | 封面：一句眉标、大标题、副标题、底部一条元信息；`hero_from`→`hero_to` 是「改前 → 改后」的大对比——`hero_from` 划掉、`hero_to` 用全页唯一的琥珀，`hero_cap` 是它下面一行小注 |
| `section` | `eyebrow` `title` `subtitle` | 分节页 |
| `statement` | `eyebrow` `text` `accent` `note` | 一页一个观点：大字陈述，`accent` 是其中要用琥珀强调的那一小段 |
| `bullets` | `eyebrow` `title` `items[{text,meta}]` `aside{label,value,note}` | 要点列表；`aside` 给右侧一块大字读数卡 |
| `two-col` | `eyebrow` `title` `cols[{title,tone,items[]}]` | 左右并排两块；`tone` = `note`/`ok`/`warn`/`danger` 决定底色与标题色 |
| `chart` | `eyebrow` `title` `chart{categories,series[{name,values}],type}` `takeaway` `caption` | 原生图表 + 右侧结论卡；`values` 里给 `null` 就是一根空柱（表示「未测」）；每根柱顶直标数值（差值大时改后的柱会贴着零点，靠标签读，别用对数轴——后端渲染会把数据标签压到轴基线） |
| `table` | `eyebrow` `title` `table{columns,col_align,rows}` `caption` | 真表格；单元格可以是字符串，也可以是 `{text,tone}` 画状态点 |
| `timeline` | `eyebrow` `title` `steps[{label,desc}]` `axis_note` | 横向时间轴，第一颗点用琥珀 |
| `closing` | `eyebrow` `title` `lines[]` `meta` | 收尾页；`lines` 里某一行写成 `{text,accent}`，就把琥珀落在那一行上 |

`chart.type` 取 `column`（默认）/`bar`/`line`。

## 设计约束（沿用 `design-system.md` 的 `--cx-*` token）

页面版的一套规矩原样搬过来：中性底、`ink/text/muted/faint` 四级灰、**单一琥珀**强调、状态三色（`ok/warn/danger` 各配 `-ink` 写字、`-wash` 做底）、一致栅格与边距、卡片只描边不投影、标题下不加装饰性强调线。数值来自 `design-system.md` §2。

幻灯片特有的：

- **一页一个观点**，每页至少一个视觉元素（图、表、读数卡或时间轴的节点）。
- **正文 ≥ 18pt**（幻灯片远比页面大，页面版的 16px 对应到这里要放大）；标签/图注可小到 12–13pt。
- **文字是原生可编辑文本框**，绝不把文字画成图片；图表走 python-pptx 原生图表 API，表格是真的 `add_table`，讲者备注写进 `notes_slide`——观众和接手改的人都能编辑。
- **留白**：左右边距 0.75"，内容起点 0.62"，页脚在 6.94" 一条发丝线下方。内容不越 6.8"。

**中文字体。** 西文 `Segoe UI`、东亚 `Microsoft YaHei`。python-pptx 的 `font.name` 只写 `<a:latin>`，CJK 不会自动落到中文字体；脚本对每处文字另写 `<a:ea>`（必要时 `<a:cs>`），图表则遍历 `c:txPr` 里的 `a:defRPr` 补上——不补就会在某些环境静默回退，甚至落到日文字形。

## 看一眼（务必看）

排版错了不会报错，文字提取也照样正确，只有看图才发现。做法：

```sh
# 1) 转 PDF 看版面。路径必须是工作区相对路径（CHEESE_WORK 下），不是 /var/tmp 绝对路径。
cheese convert deck.pptx --to pdf                 # 转出 deck.pdf，原 pptx 不动
# 2) 光栅化每一页再看
uv run --with pymupdf python - <<'PY'
import pymupdf
d = pymupdf.open("deck.pdf")
for i, p in enumerate(d):
    p.get_pixmap(matrix=pymupdf.Matrix(2, 2)).save("shots/pptx-%02d.png" % (i+1))
PY
```

`cheese convert` 是**服务端**渲染，用的字体跟本机不一定一样：服务端没有 `Microsoft YaHei` 时会静默替换成它自带的 CJK 字体（实测落到 `Noto Sans CJK`），所以产出里的中文字形可能和你指定的不是同一套。看一遍、确认没有豆腐块和错行，就算过。想并排看多页，用 pillow 拼一张 contact sheet。

## 示例

`scripts/deck-spec.example.json` → `deck.pptx`（10 页，含 8 个版式）。这是「预览改造汇报」的一组真实数据，可直接当模板改。
