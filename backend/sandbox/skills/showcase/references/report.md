# 报告

长文页面：660px 正文栏，报头、摘要、目录、散文小节、可选附录。分析、memo、设计文档、说明结论用它。

**摆法**——模板自带全部样式与两套主题，你只写正文，不用读模板、不用重写 CSS：

```bash
python3 scripts/compose.py report content.html out.html --title "预览提速"
cheese show out.html
```

- `content.html` 只放 `<main class="page">…</main>` 这一块——正文内容。宽表多的报告把开头改成 `<main class="page page--wide">`。
- 表格一律写成 `<div class="table-outer"><div class="table-wrap">…</div></div>`，紧跟 `<p class="scroll-hint">左右滑动看全部列</p>`：外层的 `.table-outer` 是窄屏右缘渐隐的锚点，两层缺一不可。
- 样式、主题、目录重建脚本都在模板里，compose 原样保留。占位文字没换掉它会报错。
- 改一份已经摆出来的报告，直接在它当前 HTML 上改，不要重新套模板。

## 顺序与组件（用不到的块整块删）

1. **报头**
   ```html
   <header class="masthead">
     <p class="eyebrow">分析 · 2026 年 10 月 4 日</p>
     <h1 class="title">预览提速</h1>
     <p class="subtitle">一句话说清结论或范围。</p>
   </header>
   ```
2. **摘要** `.lede`（底纹 + 描边，不用「左边一条竖条」）：`<div class="lede"><p class="lede__label">摘要</p><p>两三句先给结论。</p></div>`
3. **目录** `<nav class="toc">`：三节以上才留，每个 `<section>` 一条 `<li><a href="#id">标题</a></li>`；底部脚本按实际 `<h2>` 重建，静态写法也要能独立站住。
4. **小节** `<section class="section" id="s1"><h2>标题</h2><p>…</p></section>`：一个主题一节，先给结论；子标题 `<h3>`。
5. **读数**（全页唯一琥珀，只放一处）
   `<div class="readout"><p class="readout__label">…</p><p class="readout__value">0</p><p class="readout__note">…</p></div>`
   多个数改用 `<div class="stat-row">` 里若干 `.stat`（`.stat__label` / `.stat__value`）。
6. **数据表** `<div class="table-outer"><div class="table-wrap" tabindex="0" role="region" aria-label="数据表"><table>…</table></div></div>`，紧跟 `<p class="scroll-hint">左右滑动看全部列</p>`；数字列 `class="num"`，状态用 `<span class="pill pill--ok"><span class="pill__dot"></span>通过</span>`（`--ok/--warn/--danger/--neutral`）。
7. **图** `<figure class="figure">`：`.figure-outer > .figure-scroll > <svg>`（手写内联，颜色写进 `style="fill:var(--cx-chart-1)"`——`var()` 在 SVG 呈现属性里不生效），加 `.figcaption`；**同一节紧跟** `<details class="data-twin"><summary>查看数据表</summary>…</details>`。
8. **callout** `<div class="callout callout--note"><p class="callout__title">说明</p><p class="callout__body">…</p></div>`（`--warn` 注意、`--danger` 风险）。
9. **附录** `<section class="section appendix">`（可选）；**脚注** `<footer class="footnote"><p>来源与快照时间</p></footer>`。

## 内容

- 写完整句子的真实散文，别用要点堆砌代替论述；每行约 35–40 个汉字。
- 琥珀全页只给一处（报告里通常给 readout）；状态色只用于真实状态。
- **只用给出的事实**：求和、平均这类派生数标「派生」并写清怎么算的；测不到的写「未测」，不编数、不拿 0 或平均值填空。图注、脚注、页面自我描述同样守这条。
- 页名是两到四词的专名（如「预览提速」），不接破折号或冒号再加解释。
