# 改一份已有的 Word

## 里面是什么

`.docx` 是一个 zip，里面装着十几个 XML 部件。正文在 `word/document.xml`，页眉页脚在
`word/header*.xml`、`word/footer*.xml`，脚注尾注各有一份。排版几乎全在**别的部件**里：
`word/styles.xml`（样式）、`word/numbering.xml`（编号）、`word/theme/`（配色和字体）、
`word/settings.xml`，以及 `_rels/` 里部件之间的引用关系。

所以「改一句话不影响排版」是能成立的：改的那一句话在 document.xml 里，其余部件一个字节
都不用动。实测过：一份 26 个部件的文档，改完之后 25 个部件的字节和原来完全相同。

## 怎么改

```bash
SKILL=skills/documents
[ -d "$SKILL" ] || SKILL="$CLAUDE_CONFIG_DIR/skills/documents"

uv run --with lxml python3 "$SKILL/scripts/office.py" text 报告.docx
uv run --with lxml python3 "$SKILL/scripts/office.py" edit 报告.docx -o 改后.docx \
    --replace "旧的整句=新的整句" --author 芝士
uv run --with lxml python3 "$SKILL/scripts/office.py" validate 改后.docx --base 报告.docx
```

`edit` 的几个开关：

| 开关 | 作用 |
|---|---|
| `--replace 原文=新文本` | 换掉，可重复多次 |
| `--delete 原文` | 删掉（不写 `=`） |
| `--insert-after 锚点=新文本` | 在锚点后面加一句，不动原有文字 |
| `--all` / `--occurrence 2` | 多处出现时明确改哪些 |
| `--author 名字` | 修订里署的名字，写用户看得懂的 |
| `--plain` | 不留修订标记，直接改 |

`text` 的段号是排查时的坐标：一句原文找不到、或者出现了好几次，脚本报出来的段号让你直接
`text` 那一段看原文。

## 修订标记是什么

默认写成 Word 的修订（`<w:ins>` / `<w:del>`）：新句子标记为「插入」，旧句子标记为「删除」
但文字还留在文件里。用户在 Word 里能逐条接受或拒绝；在芝士的预览里也能直接看出来——
预览是 LibreOffice 渲染的，

- 插入的文字显示为**带下划线的彩色字**
- 删除的文字显示为**带删除线的彩色字**

所以「改了一句」在界面上是看得见的，不需要额外加什么开关。

`validate` 检查的不变量是：**把所有修订都拒绝之后，正文必须和原文档逐字相同**。这条过了，
才能说「除了你让我改的，原文一个字没动」。它同时会打印接受全部修订之后的正文，让你在交付前
核对一眼改出来的句子。

## 拆在几个 run 里的句子

Word 会按语言的切换、拼写检查、格式微调把一句话拆成好几个 `<w:r>`。`office.py` 会先把覆盖
到的 run 切开，只把命中的那一段放进修订，前后的文字连同它们各自的格式留在原来的 run 里——
所以改一句中间的话，句首的加粗和句尾的超链接都不会被牵连。

有两件事它不做，遇到时要知道：

- **文字被换行符隔开时**（同一个 run 里 `<w:t>前</w:t><w:br/><w:t>后</w:t>`），`前` 和
  `后` 在匹配时是连着的，替换之后那个换行还在原地。要连换行一起处理，就得改 XML 本身。
- **图片和文字写在同一个 run 里时**，脚本会先把它们拆成两个 run 再动文字，图片不会被牵连
  （这条专门测过）。

## 公式

Word 的公式是 OMML（`<m:oMath>`），不是图片也不是纯文本，pandoc 认识它：

```bash
pandoc 有公式的.docx -t markdown     # 公式会以 $LaTeX$ 出现，结构完整
```

结构能对上，只有排版空格有差别（`\alpha^2` 会变成 `\alpha^{2}`），不影响你读懂它。

**往文档里写公式不要用 pandoc 重写整份文档**——那会把排版全丢掉。正确的做法是让 pandoc
单独生成一份只含这个公式的临时文档，再把里面的 `<m:oMath>` 元素抠出来嵌进原文档：

```bash
echo '$$E = mc^2 + \frac{a}{b}$$' > /tmp/f.md
pandoc /tmp/f.md -o /tmp/f.docx          # 只为了拿到公式的 XML
```

然后用 `office.py unpack` 拆开原文档，在 `word/document.xml` 里找到要插入的段落，把
`/tmp/f.docx` 里 `word/document.xml` 的 `<m:oMath>`（外层可能套着 `<m:oMathPara>`）整个
搬进那个段落，`office.py pack` 装回。实测这样插进去的公式是 Word 原生公式，周围的标题样式
纹丝不动，整份文档也只有 `word/document.xml` 一个部件变了。
