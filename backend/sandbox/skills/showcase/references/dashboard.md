# 看板

一页看全指标：一个全页大读数、一排 KPI 小格、手写内联 SVG 的图 + 它的数据表孪生体、明细表、状态标签、提示块。KPI、监控、指标汇总用它。

**摆法**——模板自带全部样式，你只写正文：

```bash
python3 scripts/compose.py dashboard content.html out.html --title "二季度营收看板"
cheese show out.html
```

- `content.html` 只放 `<main class="page">…</main>` 这一块。列很多的看板把开头改成 `<main class="page page--wide">`（1100px）。
- 表格一律写成 `<div class="table-outer"><div class="table-wrap">…</div></div>`，紧跟 `<p class="scroll-hint">左右滑动看全部列</p>`：外层的 `.table-outer` 是窄屏右缘渐隐的锚点，两层缺一不可。
- 样式、主题、打印规则都在模板里，compose 保留。没换掉的占位数字会让它报错。
- 改一份已有的看板，直接在它当前 HTML 上改，不用重新套模板。

## 顺序与组件

1. **报头** `<header class="masthead">`，含 `.eyebrow`（周期）/ `.title` / `.subtitle`——`.subtitle` 先写一句**从数据里读出来的结论**。
2. **摘要区** `<div class="summary">`：
   - **大读数**（全页唯一琥珀，只放一个）：`<div class="readout"><p class="readout__label">本周合计</p><p class="readout__value">0</p><p class="readout__note">…</p></div>`
   - **KPI 格** `<div class="stat-row">` 里 2–5 个 `<div class="stat"><p class="stat__label">…</p><p class="stat__value">128</p><p class="delta delta--good">&#x25BC; 3 个</p></div>`。涨跌按**含义**上色（下降是好事用 `.delta--good` 配 `&#x25BC;`）；**整排要么都带 delta 要么都不带**。
3. **图** `<section class="section"><h2>趋势</h2><figure class="figure">…</figure></section>`：`.figure-outer > .figure-scroll > <svg>`（手写内联，颜色写 `style="fill:var(--cx-text)"`），`.figcaption`，再紧跟 `<details class="data-twin"><summary>查看数据表</summary><div class="table-outer"><div class="table-wrap"><table>…</table></div></div><p class="scroll-hint">左右滑动看全部列</p></details>`。图和表**同一比例尺**、同一节。不要改 svg 的 `min-width/max-width`（窄屏读性的保底，模板另有 480px 处理）。
4. **明细表** `<section class="section"><h2>明细</h2><div class="table-outer"><div class="table-wrap" tabindex="0" role="region" aria-label="明细表"><table>…</table></div></div><p class="scroll-hint">左右滑动看全部列</p></section>`。十来行以内，长尾并成「其他」；数字列 `class="num"`；状态用 `.pill--ok/--warn/--danger/--neutral`，同一列只用同一种。
5. **提示块** `<div class="callout-stack">` 里 `.callout--note / --warn / --danger`（各 `.callout__title` + `.callout__body`）。没有就不写。
6. **脚注** `<footer class="footnote"><p>数据来源与快照时间</p></footer>`。

## 内容

- 顶部先给一句结论（`.subtitle`），数字在后。**没有时间维度就不要造趋势**——用横条排行或只放 KPI + 明细。
- **每个占位数字都换成真实的，绝不编一个**。图表刻度和数据表数字要**同一套**；测不到的显式标「未测」。
- 区间末日是今天时，在提示块和脚注注明当日数据可能未走完整天。
- 派生数（合计、日均、环比）标「派生」并写清怎么算；图注、脚注、自我描述同样守这条数据纪律。
