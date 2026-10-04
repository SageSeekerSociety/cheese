# 说明

教人「它怎么运作」：一句导读说清读完会弄懂什么，接着编号步骤（或小节），每步一段短文配一个视觉（通常内联 SVG），最后用读者新学到的说法小结。讲概念、走流程、解释机制用它。

**摆法**——模板自带全部样式：

```bash
python3 scripts/compose.py explainer content.html out.html --title "一份文件怎么出现在预览里"
cheese show out.html
```

- `content.html` 只放 `<main class="page">…</main>` 这一块。
- 表格一律写成 `<div class="table-outer"><div class="table-wrap">…</div></div>`，紧跟 `<p class="scroll-hint">左右滑动看全部列</p>`：外层的 `.table-outer` 是窄屏右缘渐隐的锚点，两层缺一不可。
- 样式、主题、目录脚本都在模板里，compose 保留。占位文字没换掉会报错。
- 改一份已有的说明页，直接在它当前 HTML 上改，不用重新套模板。

## 选一种结构（留一种、连外层一起删掉另一种）

- **编号步骤**（默认）`<ol class="steps" role="list">`：讲「怎么运作」这类有真实先后的概念。
  ```html
  <li class="step"><p class="step__num" aria-hidden="true"></p>
    <div class="step__body">
      <h2>这一步发生的事</h2>
      <p>一段短文，说清发生了什么、为什么。</p>
      <figure class="figure"><div class="figure-outer"><div class="figure-scroll"><svg …></svg></div></div>
        <p class="scroll-hint">窄屏可左右滑动看图</p>
        <figcaption>图 1 · 读者该从图里得到什么。</figcaption>
        <details class="data-twin"><summary>查看数据表</summary>…</details>
      </figure>
    </div>
  </li>
  ```
  视觉里带数值才配 `<details class="data-twin">`，纯示意图删掉它。图形词汇全程一致：**方块=东西，箭头=动作/因果**，当前步强调的方块用 `style="fill:var(--cx-fill-2);stroke:var(--cx-ink)"`，其余 `var(--cx-surface)` + `var(--cx-line-2)`；颜色写进 `style`（SVG 呈现属性里的 `var()` 不生效）。代码放 `<pre class="code"><code>…</code></pre>`，别塞进 SVG。
- **小节** `<div class="topics">` 里若干 `<section class="topic"><h2>…</h2><p>…</p></section>`：顺序松、代码占更多分量时用；开头可放一张宽的架构图。

其余块：**报头** `.masthead`（`.title` 写成读者心里那个问题）；**导读** `.lede`；**目录** `.toc`（四节以上才留）；**小结** `<ul class="recap">`（用读者的新说法重述，不是抄正文）；**脚注** `.footnote`。

## 内容

- **导读要能兑现**：开头说清会弄懂什么，正文步步兑现，兑现不了就别许。
- **一步一个意思**，切在材料的关节上；概念之间没有先后就用小节型，别硬套 01/02/03。步骤型一般 3–7 步。
- 散文说清，每行约 35–40 字；术语首次出现就解释，不留没解释的缩写。
- 每个 `<svg>` 给 `role="img"` 和一句 `aria-label`；图内文字 12px 起，`viewBox` 给最外侧标签留位置；带数值的按同一比例尺画，测不到的显式标「未测」。
- 只用给出的事实，派生数标「派生」并写清算法；图注、脚注、自我描述同样守这条数据纪律。
