# 改一份已有的 PowerPoint

## 里面是什么

`.pptx` 也是 zip。每张幻灯片一份 XML：`ppt/slides/slide1.xml`、`slide2.xml`……版式在
`ppt/slideLayouts/` 和 `ppt/slideMasters/`，主题配色在 `ppt/theme/`，图片在 `ppt/media/`。
一页幻灯片上所有能放字的地方（标题、正文框、文本框、表格）都是 `slideN.xml` 里的 `<a:p>`
段落，所以 `office.py text` 把它们按顺序全部打印出来，段号就是这页里的第几个段落。

## 怎么改

```bash
SKILL=skills/documents
[ -d "$SKILL" ] || SKILL="$CLAUDE_CONFIG_DIR/skills/documents"

uv run --with lxml python3 "$SKILL/scripts/office.py" text 汇报.pptx
uv run --with lxml python3 "$SKILL/scripts/office.py" edit 汇报.pptx -o 改后.pptx \
    --replace "旧的整句=新的整句" --plain
uv run --with lxml python3 "$SKILL/scripts/office.py" validate 改后.pptx
```

**PowerPoint 里没有修订标记这回事**（修订是 Word 的机制），所以 `.pptx` 必须给 `--plain`；
不给的话脚本会拒绝执行并告诉你原因。也就是说改幻灯片是**直接改**，用户拿到的就是改完的样子，
没有「接受/拒绝」这一步。改之前把改动告诉用户，改完再说一遍改了什么。

`text` 会带上部件名（`--- ppt/slides/slide2.xml`），因为同一句话可能出现在多页里。出现了
好几次时，脚本报的位置也带着部件名和段号。

## 交付前

- 跑 `validate 改后.pptx --base 原文件`：它会列出真正变了的部件（应该只有
  `ppt/slides/slideN.xml`），并确认包本身没坏。PowerPoint 对 XML 比 Word 挑剔，一个坏的
  部件直接就是「无法打开演示文稿」。
- 幻灯片上的一句话常常被拆成好几个 run（格式刷、中英混排都会拆），所以 `text` 里看到的
  一句话，在文件里可能不是一个整体。按 `text` 输出的原文照抄，找不到就换更短的片段。
- 只改了文字的话版式不会动；但如果新文字比原来长很多，文本框可能装不下——PPT 不会自动
  缩小字号，也不会重新排版，溢出看起来就是文字被框裁掉。改长句子时提醒用户检查这一页。
