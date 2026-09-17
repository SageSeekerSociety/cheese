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
| `--all` / `--occurrence 2` | 多处出现时说清改哪些：全改，或者只改第二处 |
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

注意它管不到的那一半：多改的那一处也是格式正确的修订，拒绝全部之后照样回到原文，所以
`validate` 会通过。**改了几处、改的是哪几处，要靠 `revisions` 的清单核对。**

## 逐条接受或拒绝

```bash
uv run --with lxml python3 "$SKILL/scripts/office.py" revisions 改后.docx
```

```
改后.docx：3 处修订
[  1] 把 '30 天' 改成 '60 天'    （word/document.xml 第 4 段，芝士）
[  2] 加了 '并按季度复核。'      （word/document.xml 第 4 段，芝士）
[  3] 删了 '（暂定）'            （word/document.xml 第 9 段，芝士）
```

一次替换在 XML 里是一个 `<w:ins>` 加一个 `<w:del>`，清单把它们合成一行——因为用户要决定的
是「这处改动要不要」，而不是分别处理新文字和旧文字；只接受一半的结果是新句子进来了、旧句子
还留在文件里。

用户说「第二处不要」时：

```bash
uv run --with lxml python3 "$SKILL/scripts/office.py" revisions 改后.docx -o 定稿.docx \
    --accept 1 --reject 2
```

接受一处插入等于拆掉 `<w:ins>` 外壳保留文字，接受一处删除等于把文字一并删掉，拒绝则相反。
没点到的那几处原样留在文件里。`--accept-all` / `--reject-all` 是全部。

**序号只对你刚列出来的那份清单有效。** 处理完之后剩下的修订会重新从 1 数起，所以要按清单
操作就在同一条命令里把该处理的都点掉，或者每次重列一遍。

`--json` 输出同样的清单，给需要拿它做界面的地方用。页码不在里面：XML 里没有页的概念，
页要等排版之后才存在，段号是这里唯一靠得住的坐标。

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

**读**是可靠的：公式不会丢、不会变成图片，结构完整。

**但转出来的 LaTeX 不等于原来的 LaTeX。** 实测七种论文里常见的形状，只有两种逐字一致，
其余的意思没变、写法变了：`\mathrm{d}x` 变成 `dx`，`\mathrm{softmax}` 丢掉外面的
`\mathrm`，`\begin{aligned}` 和 `\begin{cases}` 都变成 `\begin{matrix}`，`\to` 变成
`\rightarrow`，`(x)` 变成 `\left(x\right)`。

所以：**用 pandoc 读公式可以，用它把公式转出去再转回来不行**。用户要的是「原文里那个公式
照抄到新文档」时，不要走 LaTeX 中转，直接把 `<m:oMath>` 节点搬过去（见下）。

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

## 改格式：先改样式，不要逐处改行内属性

用户说「小标题都改成四号加粗」时，正确的做法是改 `word/styles.xml` 里那个样式的定义，
不是把文档里每一处小标题的行内属性挨个改掉。

理由和 Word 用户自己的正确做法是同一个：样式改一处影响全篇，行内属性只影响你碰到的那一处。
逐处改的结果是漏掉一两处（用户翻到那一页才发现），而且以后再改还得重新逐处找一遍——文档
从此没有「小标题长什么样」这个单一答案了。

```bash
uv run --with lxml python3 "$SKILL/scripts/office.py" unpack 报告.docx 拆开
# 改 拆开/word/styles.xml 里那个 <w:style w:styleId="Heading2"> 的 <w:rPr>
uv run --with lxml python3 "$SKILL/scripts/office.py" pack 拆开 改后.docx
```

例外只有一种：用户要的就是**某一处**不一样（「这一段的这几个字标红」）。那本来就是行内属性
该管的事。

## 遇到复杂元素：要求碰到了才说，碰不到不提

文档里的东西不是「能改」和「不能改」两类，是一道梯度：

| 元素 | 读 | 改 |
|---|---|---|
| 公式 | 可以，`<m:oMath>` 是结构化 XML | 可以，见上一节 |
| 文本框 | 可以，`<w:txbxContent>` 里面是普通 run | 可以，和改正文一样 |
| 图片 | 可以，`word/media/` 下是真实图片文件 | 可以替换 |
| 图表 | 可以，`word/charts/chartN.xml` 的 `c:numCache` / `c:strCache` | 可改，但**必须改两处**（见下） |
| SmartArt | 文字可以读，在 `word/diagrams/data1.xml` | 困难 |
| OLE 嵌入对象 | 看类型，内嵌的 `.xlsx` 可读 | 不可 |

**申报规则：只在用户要求的改动正好落在困难或不可修改的元素上时才说明，并说清界线在哪。**
不要预先申报——没被要求改的元素，没有被碰到是默认行为，不是成果。「我没有动您的 SmartArt」
这种话只会让用户以为你动过别的东西。

同样不能做的是静默跳过：改不了就说改不了，不要交一份「看起来改完了」的文件。

### 图表：缓存和内嵌工作簿要一起改

`word/charts/chartN.xml` 里存着一份数据缓存（`c:numCache` 的数值、`c:strCache` 的标签），
它的作用是让图表脱离数据源也能画出来。同一份数据还在 `word/embeddings/` 下的一个内嵌
`.xlsx` 里。

**只改一处，两边就对不上了**：Word 按缓存画图，用户双击图表编辑数据时看到的是内嵌工作簿里
的旧数字。文件正常打开，图也画得出来，错要等到有人去核对才发现。

所以改图表数据是三步：改 `chartN.xml` 的缓存、改 `word/embeddings/` 里那份工作簿（用
`openpyxl`）、重新打包。只改缓存的话，把这件事告诉用户，别默认他不会去点那个图。

### SmartArt 为什么算困难

`word/diagrams/data1.xml` 里是文字，但 Word 画出来的样子存在同目录的 `drawing1.xml`——那是
一份预渲染结果。只改 data1.xml，文字在编辑面板里变了，画面上还是旧的。要两份一起改，而
`drawing1.xml` 的坐标是按原文字长度算出来的。用户明确要求改 SmartArt 里的文字时，说清这一点，
问他能不能接受改完重排，或者由他在 Word 里改。
