# 幻灯片

16:9 的单文件 HTML 演示：一张幻灯片一个 `<section class="slide" id="…">`，演讲者备注是该 section 最后一个 `<aside class="notes">`（屏幕不显示，按 N 看）。键盘/点击翻页、页码、深链、打印每页一张。汇报、演示、路演默认用它。

**摆法**——舞台缩放、翻页、备注、深链、打印都在模板里：

```bash
python3 scripts/compose.py slides content.html out.html --title "预览提速"
cheese show out.html
```

- `content.html` 只放**一串 `<section class="slide">`**（模板 `CONTENT` 标记之间就是它们）——不含 `<div class="stage">`，也不含脚本。
- 给了 `--title` 时，compose 会按你写出的 section 重建开头那份 `deck-index`（目录元数据），标题也一起换。
- 想导成 PDF / 要 `.pptx` 文件本身：读 `references/pptx.md`。

## 版式（`class="slide slide--<版式>"`）

每张选一个版式，**每页都要有一个视觉**，琥珀每页至多一处。页码/缩放由舞台脚本管，字号按 1280×720 写。

| 版式 | 用在 | 视觉 |
| --- | --- | --- |
| `slide--cover` | 封面：名字 + 最要紧的对比 | `.stat-hero`（`.stat-hero__from` → `__to`，改后用琥珀） |
| `slide--statement` | 全页最要紧的一个数 | `.statement__value`（琥珀大数） |
| `slide--bullets` | 三五条要点 + 一张视觉 | 左 `.bullets`、右 `.visual`（内联 SVG 或截图） |
| `slide--compare` | 两栏对照（改前/改后、A/B） | 左右 `.compare__col`，中间发丝线；未测写「未测」 |
| `slide--chart` | 一个趋势/排行 + 一句结论 | 左 `.chart`（手写内联 SVG，写法见 `references/data-table.md` 图表规则），右 `.takeaway` |
| `slide--table` | 编号清单、明细 | `.slide-table`，数字列右对齐、等宽数字 |
| `slide--section` | 分节页 | `.section__num` + `.section__title`，可带 `.dirlist` |
| `slide--timeline` | 时间线 / 下一步 | 一排 `.tl` 节点，当前步 `.tl--now` 用琥珀 |
| `slide--closing` | 收尾：请谁拍板什么 | `.roles` 三张卡，最要紧那张 `.role--accent` |

一个版式不够时，从 `design-system.md` §4 取组件拼进 `.slide__body`，别另造一套。

## 内容

- **一页一个意思**：一页说不完就拆两页，别缩字号硬塞。**先讲最重要的**，封面就带结论。
- **每页一个视觉**：纯文字页没用上这个版式；数字、图表、对照、时间线、表格都是视觉。
- 正文 ≥22px、标题 ≥38px（1280×720 上）；留白要足。**不要标题下的装饰横线、不要居中排、不要圆角卡片左边加竖条**。
- 不编数字：分清测出来的、推出来的、猜的，测不到写「未测」，缺的写「未定」。
- **数据纪律覆盖讲者备注**：备注里引用数字同样只用给出的事实，派生数（求和、平均）标「派生」并写清算法。

## 看一次

摆之前渲染一次：每页截一张，确认没有文字溢出/被裁。
```bash
source /var/tmp/pw/env.sh
uv run --quiet --with playwright --python 3.13 python 看.py   # 视口 1280×720，page.goto(URL+"#id") 逐页截
```
量每页 `slide.scrollHeight - slide.clientHeight`，>0 就是溢出了盒子（舞台上 `overflow:hidden`，会被悄悄裁掉）。**看一次、改一轮就摆，不搭反复截图循环**——没有浏览器时别装，改成通读 HTML 确认。

窄屏（≤720px）模板会自动从缩放舞台切成纵向堆叠的阅读视图（每页一块、正文 ≥15px），不用你写媒体查询。
