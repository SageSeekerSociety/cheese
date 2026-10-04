# 方案对比

给团队拍板的对比页：推荐顶到最前，候选并排，按标准逐项打分，可选一张对比图，再列风险和待决事项。选型、方案拍板用它。

**摆法**——模板自带全部样式与组件：

```bash
python3 scripts/compose.py compare content.html out.html --title "预览路线选型"
cheese show out.html
```

- `content.html` 只放 `<main class="page">…</main>` 这一块。
- 表格一律写成 `<div class="table-outer"><div class="table-wrap">…</div></div>`，紧跟 `<p class="scroll-hint">左右滑动看全部列</p>`：外层的 `.table-outer` 是窄屏右缘渐隐的锚点，两层缺一不可。
- 样式、主题、组件都在模板里，compose 保留。没换掉的占位文字会让它报错。
- 改一份已有的对比页，直接在它当前 HTML 上改，不用重新套模板。

## 顺序与组件

1. **报头** `<header class="masthead">`：`.eyebrow`（可选）/ `.title`（专名，如「预览路线选型」，不是「方案对比」）/ `.subtitle`（要回答的那个问题）。
2. **推荐**（顶到最前，别留到末尾）：`<div class="lede"><p class="lede__label">推荐</p><p>推荐 <strong class="pick">方案名</strong>，因为…；兜底是…。</p></div>`——`.pick` 是全页唯一琥珀。
3. **候选** `<div class="options">` 里若干 `.option`，推荐的排最前：
   ```html
   <div class="option">
     <div class="option__head"><h3>方案 A</h3><span class="pill pill--neutral"><span class="pill__dot"></span>推荐</span></div>
     <p class="option__tagline">一句话说清它是什么。</p>
     <ul><li class="pro">利：…</li><li class="con">弊：…</li></ul>
   </div>
   ```
   2–4 张最清楚。每个方案的利弊都真写，别把陪跑的写成稻草人。
4. **打分表** `<div class="table-outer"><div class="table-wrap" tabindex="0" role="region" aria-label="方案评分对照表"><table>…</table></div></div>` + `.scroll-hint`：一行一个 `<td class="crit">标准</td>`，一列一个方案；评分 `<td class="rating hi" data-label="方案 A">优</td>`（`hi`/`mid`/`lo`），测不到写 `<td class="rating na" data-label="方案 B">未测</td>`。`data-label` 写该列方案名——窄屏（≤480px）模板把这张评分表折成**一行一张卡、标准当标题**，`data-label` 就是每格的方案名，第二个方案不用横滑就能看到。表头或下方写清**评分口径**，列数与候选对齐。
5. **对比图**（可选）`<figure class="figure">`：`.figure-outer > .figure-scroll > <svg>`（手写内联，颜色写 `style="fill:var(--cx-text)"`），加 `.figcaption`，同一节紧跟 `<details class="data-twin"><summary>查看数据表</summary>…</details>`。没有图整块删掉。
6. **风险** `<div class="callout-stack">` 里 `.callout--warn`，每条一句说清风险是什么、怎么应对或降级。没有真实风险就删。
7. **待决事项** `<table>` 三列：待决事项 / 选项 / 由谁定（带期限）。写具体的人和时间，别写「尽快」；三列都短，「由谁定」要在 400px 内先看得见。
8. **脚注** `<footer class="footnote"><p>数据来源与口径</p></footer>`。

## 内容

- **结论在前**：读者只读前两行也该知道你的建议。
- **打分要有口径**：标准是什么、谁定的；能引实测数据就引，引不到就说是估计；没测出的写「未测」，不用默认值或平均值填空。
- 图用**单一比例尺**，测不到的值在图上显式标「未测」（别画默认长度的柱）；图注口径和图形一致。
- 琥珀只给一处（推荐名）。派生数（合计、差值）标「派生」并写清算法；图注、脚注、自我描述同样守这条数据纪律。
