# 模板：从一份现成的结构和样式出发做新文件

模板管的是**成果长什么样**（结构、样式、版式），不管怎么做——做法在技能里。两种来源，
做法不同：

| 来源 | 起点 | 填内容 |
|---|---|---|
| 平台的标准模板（报告、方案、周报、项目汇报、调研汇报、数据统计分析） | `cheese template new` | `template.py fill` 填【占位】，内容多于占位时再按下面补 |
| 用户给的文件（「照这份的格式做一份新的」） | 资料库里的原件 → 房间里的一份**副本** | 按 `template.py inspect` 看到的结构和样式名重新组织内容 |

两种都**不动原件**：标准模板在平台上，用户的原件在资料库里是只读的，你写的永远是
房间里的一份新文件。

## 先看它由什么构成

```bash
SKILL=skills/documents
[ -d "$SKILL" ] || SKILL="$CLAUDE_CONFIG_DIR/skills/documents"
python3 "$SKILL/scripts/template.py" inspect 模板.docx
```

它列出：Word 的每一段用什么样式、表格的大小和表头、【占位】；幻灯片每一页的文字框和
图片；表格的每张工作表、首行、公式数和图表数。最后一行如果写着「这套工具带不过去」，
说的是 SmartArt、内容控件、域代码、数据透视表、宏这类东西——**动手前就告诉用户**：新
文件里这部分会保持原样不动或者没有，而不是等他打开才发现。

## 标准模板

```bash
cheese template list                              # 看有哪些
cheese template new weekly 文档/周报.docx         # 在房间里建一份，并取到本地同一个路径
```

`new` 建好之后用户马上就能在房间里看到、打开编辑器。接着填占位：

```bash
cat > 填写.json <<'EOF'
{"【项目名称】": "知是", "【起止日期】": "9/22–9/26", "【姓名】": "芝士"}
EOF
python3 "$SKILL/scripts/template.py" fill 文档/周报.docx -o /tmp/周报.docx --values 填写.json
mv /tmp/周报.docx 文档/周报.docx
cheese show 文档/周报.docx --note "按周报模板填好了本周内容"
```

`fill` 只改文字节点里的那几个字，样式、编号、页眉页脚、图表原样保留。它**不答应半成品**：
还留着【】没填、或者给的值在文件里找不到，就报出来并退出 1。故意留作草稿时加
`--allow-leftover`，并告诉用户哪几处还空着。

内容比占位多（三条发现、七行数据），占位填完后在**你自己这份新文件**上补：

- Word：`python-docx` 打开这份新文件，按 `inspect` 列出的样式名加段落，比如
  `doc.add_paragraph(text, style="Heading 2")`。这是你新做的文件，不是用户的，所以可以
  打开再存——「改已有文件不要打开再另存」那条说的是用户交来的文档。
- 幻灯片：`python-pptx` 用同一个版式加页：`prs.slides.add_slide(prs.slides[1].slide_layout)`。
- 表格：`openpyxl` 往「数据」表里写行；数据超出汇总公式的范围（模板是第 2–6 行）就把
  公式的范围一起改大，改完照 `sheets.md` 的「交付前」三步走。

## 用户的文件当模板

「照这份的格式做一份新的」：

```bash
cheese library get 去年的报告.docx                       # 原件只读，落在 ~/attachments/library/
cp ~/attachments/library/去年的报告.docx 文档/今年的报告.docx
python3 "$SKILL/scripts/template.py" inspect 文档/今年的报告.docx
```

用户在房间里也能自己做这一步：在编辑器里打开资料库里的那份，按「复制一份来编辑」。

然后看原件有没有【】占位：

- **有**：和标准模板一样 `fill`。
- **没有**（多数情况）：它是一份写满了去年内容的真文档。用 `python-docx` 打开副本，保留
  `sectPr`（页面设置、页眉页脚）清掉正文，再按 `inspect` 里的样式名把今年的内容一段段
  加回去；表格照原表的列和样式重建。幻灯片保留母版和版式、删掉原来的页、用同样的版式
  加新页。表格保留工作表结构和格式，只换数据。

做完对照 `inspect` 再跑一遍新文件：样式名还是那几个、表格列数一致、页面设置没变。
原件里有而新文件没带过来的（`inspect` 报出来的那几类），交付时明说。

## 交付前

- 新文件再 `inspect` 一次：没有残留的【】，结构和模板对得上。
- 内容读回来核对（Word 用 `pandoc`，表格照 `sheets.md` 三步），再 `cheese show --note`。
- 用户的原件一个字节没动：它在资料库里，你改的是房间里的副本。
