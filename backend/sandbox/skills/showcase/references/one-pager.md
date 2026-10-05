# 一页纸

正好一张 A4 的结论页：结论、一个读数、一张小图 + 数据表、几条要点、风险与下一步。汇报、摘要、给会上看的一页结论用它。要打印成 PDF 时读 `references/pdf.md`。

**摆法**——模板（`.sheet` 就是那张 A4）自带全部样式：

```bash
python3 scripts/compose.py one-pager content.html out.html --title "预览提速"
cheese show out.html
```

- `content.html` 只放 `<article class="sheet">…</article>` 这一块。
- 表格一律写成 `<div class="table-outer"><div class="table-wrap">…</div></div>`，紧跟 `<p class="scroll-hint">左右滑动看全部列</p>`：外层的 `.table-outer` 是窄屏右缘渐隐的锚点，两层缺一不可。
- 样式、主题、打印规则都在模板里，compose 保留。占位文字没换掉会报错。
- 改一份已有的一页纸，直接在它当前 HTML 上改，不用重新套模板。

## 顺序与组件

1. **报头** `<header class="masthead">`：`.eyebrow`（类型与日期）/ `.title`（专名或那个问题本身）/ `.conclusion`（一句话结论，带具体数字）。**结论在最前**。
2. **读数**（全页唯一琥珀，只放一个）：`<div class="readout"><p class="readout__label">读数标签</p><p class="readout__value">0.91 秒</p><p class="readout__note">跟什么比。</p></div>`；改用 ≤3 个 stat tiles 时换成 `.stat-row` 里若干 `.stat`（二选一）。
3. **图与数据** `<section class="section"><h2>图与数据</h2><figure class="figure">…</figure></section>`：`.figure-outer > .figure-scroll > <svg>`（手写内联；单系列用 `style="fill:var(--cx-text)"`，多条分类才依次取 `--cx-chart-1..6`；`var()` 在 SVG 属性里不生效），加 `.figcaption`，紧跟 `<details class="data-twin"><summary>查看数据表</summary>…</details>`。图用同一比例尺，测不到的显式标「未测」。
4. **要点 + 风险** `<div class="grid2">` 两栏：
   - `<ol class="takeaways">` 3–5 条，每条一句、先给结论。
   - `.callout--warn`（风险）+ 一句「下一步：…；负责人与拍板人写在这里，日期未定写未定」。
5. **脚注** `<footer class="footnote"><p>数据来源与快照时间</p></footer>`。

## 内容与打印

- 一页纸只有一页：内容超了要删内容、缩字号或调 `.sheet` 的 `gap`，别让它溢到第二页。打印前**一定要看一次**（页数、中文是否变方框，见 `references/pdf.md`）。
- 结论、读数、要点都要有具体数字；**只用给出的事实**，派生数（求和、平均）标「派生」并写清算法，测不到写「未测」。
- 图注、脚注、自我描述同样守这条数据纪律；琥珀全页只一处。
