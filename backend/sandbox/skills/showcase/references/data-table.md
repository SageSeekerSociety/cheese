# 数据表格

可排序、可筛选的记录表：CSV、查询结果、目录、一串实测记录。版式是「筛选框 + 点列头排序的紧凑表 + 行数提示」，数据以 JSON 嵌在页面里，模板自带的渲染器画行、排序、筛选。

**摆法**——排序/筛选机制都在模板里，你只写内容：

```bash
python3 scripts/compose.py data-table content.html out.html --title "预览用时实测记录"
cheese show out.html
```

`content.html` 里放**三块**（模板 `CONTENT` 标记之间就是它们）：
1. `<main class="page">…</main>`——报头、可选摘要/读数/小结图、筛选行、`<table id="dt">`（空表头，行由渲染器填）、可选 callout、脚注。
2. `<script type="application/json" id="dt-columns">[…]</script>`——列定义。
3. `<script type="application/json" id="dt-rows">[…]</script>`——数据行。

**列与行**（严格 JSON：双引号、无尾逗号、无注释）：
- 列：`{ "key": "name", "label": "名称", "type": "text" }`，`type` 是 `"text"` 或 `"num"`（右对齐、按数值排序）；`"decimals"` 可选。顺序 = 显示顺序。
- 行：`{ "name": "占位一行", "count": 0 }`，按列 key 组织。`"num"` 列的值必须是**数字**（`1234.5`，不是 `"1,234.50"`），单位写进列名。
- 缺的值用 `null`（或干脆不给这个 key），**绝不用 `0`、`"N/A"`、`"-"`**；测不到就留空，并在 callout/脚注说明为什么空。
- 日期放 `"text"` 列，格式 `2026-07-08`（按字母排即按时间排）；只知道范围就写范围。
- 字符串里的 `</` 转义成 `<\/`、`<!--` 转义成 `<!--`（都是合法 JSON，否则会提前结束 `<script>`）。整个数据集嵌进来（几千行以内）；再多就聚合，并在脚注说明砍了什么。

## 组件（`main` 里，用不到的整块删）

- **报头** `<header class="masthead">`：`.eyebrow` / `.title`（同时是浏览器标签名）/ `.subtitle`（说清这张表是什么、给谁看）。
- **摘要** `.lede`、**读数** `.readout`（全页唯一琥珀，只一处）、**KPI 格** `.stat-row`——都可选。
- **小结图**（可选）`<figure class="figure">` 放表格标题之后、筛选行之前；手写内联 SVG，颜色写 `style="fill:var(--cx-text)"`（`var()` 在 SVG 属性里不生效）。**它的数据表就是下面那张可排序表**，不用另贴孪生体。
- **筛选行/表**（原样保留，别改 id 与 class）：
  ```html
  <div class="table-toolbar">
    <label for="dt-filter" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)">筛选行</label>
    <input class="filter" id="dt-filter" type="search" placeholder="筛选行&hellip;" autocomplete="off">
    <span class="count" id="dt-count" aria-live="polite">0 行</span>
  </div>
  <div class="table-outer"><div class="table-wrap" tabindex="0" role="region" aria-label="数据表"><table id="dt"><thead><tr></tr></thead><tbody></tbody></table></div></div>
  <p class="scroll-hint">左右滑动看全部列</p>
  ```
- **callout** `.callout--note / --warn / --danger`；**脚注** `<footer class="footnote">`。

## 内容与窄屏

- 只改 token 的**值**可以，但要四个 scope 一起改（亮色 `:root`、两个暗色块、`@media print`）；`dt`、`dt-filter`、`dt-count`、`arrow`、`sorted`、`num`、`empty` 这些 id/class 和结尾的渲染器 `<script>` **不要动**，改了排序/筛选/主题会坏。
- 表头、占位、行数、空状态都是界面文字：用人们认得的东西命名，主动语态。空状态渲染器已写好，别自己再加。
- 表格一律包在 `<div class="table-outer"><div class="table-wrap">…</div></div>` 里，再跟 `<p class="scroll-hint">左右滑动看全部列</p>`——右缘渐隐靠外层，两层缺一不可。窄屏：`.table-wrap` 横滑并给右缘渐隐 + 一行提示，**最该看的列要在 400px 内先出现**；列多到读不动就改成分组堆叠卡。
- 只用给出的事实，派生数（求和、平均）标「派生」并写清算法；脚注、图表标注同样守这条数据纪律。
