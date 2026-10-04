# 计划

实施计划、技术方案、RFC、架构提案：按背景、方案、阶段、验证四节走，另有可选图和风险、执行与拍板。要别人照着做、或要团队过一遍再开工的活用它。

**摆法**——模板自带全部样式与组件：

```bash
python3 scripts/compose.py plan content.html out.html --title "截图能力接入"
cheese show out.html
```

- `content.html` 只放 `<main class="page">…</main>` 这一块。
- 表格一律写成 `<div class="table-outer"><div class="table-wrap">…</div></div>`，紧跟 `<p class="scroll-hint">左右滑动看全部列</p>`：外层的 `.table-outer` 是窄屏右缘渐隐的锚点，两层缺一不可。
- 样式、主题、目录脚本都在模板里，compose 保留。占位文字没换掉会报错。
- 改一份已有的计划页，直接在它当前 HTML 上改，不用重新套模板。

## 顺序与组件

1. **报头** `<header class="masthead">`：`.eyebrow`（类型/日期）/ `.title`（专名，如「截图能力接入」）/ `.subtitle`（一句话说清做什么、到什么程度）。
2. **摘要** `.lede`（可选）+ **读数** `.readout`（可选，全页唯一琥珀，只一处）。
3. **目录** `<nav class="toc">`：三节以上才留，每个 `<section>` 一条；静态写法也要能独立站住。
4. **背景** `<section class="section" id="context"><h2>背景</h2><p>…</p></section>`：这件事从哪来、现在卡在哪、为什么值得做，写完整段落。
5. **方案** `<section id="approach">`：整体路径是什么、为什么这样选；对比选型用表或 `.callout--note`。
6. **阶段** `<ol class="phases">`，每个阶段是真序列里的一步：
   ```html
   <li class="phase">
     <div class="phase__head"><h3 class="phase__name">接入截图 API</h3><span class="pill pill--warn"><span class="pill__dot"></span>进行中</span></div>
     <p class="phase__note">做什么、交出什么。</p>
   </li>
   ```
   pill 三色：已完成 `.pill--ok`、进行中 `.pill--warn`、未开始 `.pill--neutral`。状态照实标，别把没开始的标成进行中。
7. **验证** `<section id="verification">`：口径一段话 + `<ul>` 里若干**可核对的动作**（截一张图、跑一轮评分、量一个尺寸），不是「确认没问题」。
8. **图**（可选，有真实数据、图比散文更清楚时才放）：`<figure class="figure">` 内 `.figure-scroll > <svg>`（手写内联，颜色写 `style="fill:var(--cx-text)"`），加 `.figcaption`，同一节紧跟 `<details class="data-twin"><summary>查看数据表</summary>…</details>`。
9. **风险**（可选）`.callout-stack` 里 `.callout--warn`，每条配应对或降级办法；没有真风险就删。
10. **执行与拍板** `<dl class="facts">` 里 `<div class="fact"><dt>负责人</dt><dd>…</dd></div>`；时间没定写「未定」，**不编日期**。
11. **脚注** `<footer class="footnote"><p>来源 / 版本 / 快照时间</p></footer>`。

## 内容

- **结论在前**：`.subtitle` + `.lede` 先把做法说清。阶段编号是真实顺序才用有序列表，不是序列就改用普通小节。
- 写完整句子的散文，别用要点代替论述；每行约 35–40 字。
- **验证可核对、风险可应对**；引真实数据能引就引，引不到就说是估计。
- 派生数（工期合计、比例）标「派生」并写清算法；测不到的写「未测」、没定的写「未定」；图注、脚注、自我描述同样守这条数据纪律。
