---
name: doc-apps
title: 小程序的写法
description: 文档里小程序的代码怎么写。随 cheese-docs 技能发出，在里面是 references/apps.md。
---

# 小程序的写法

读者要自己动手才看得懂时才用小程序：拖动一个参数看结果怎么变、切换几种方案对比、一步一步播放一个过程、填数试算。能用表格、图表块、时间线说清楚的，不用它。一份文档里一般不超过两个。

## 怎么交

1. 用 `cheese_app_save` 交代码（`code`）和标题（`title`）。平台先在浏览器里运行一遍，手机宽和桌面宽、浅色和深色各一次：报错、什么都没画出来、高度一直在长，就不存，返回原因。
2. 新建时返回一个块。把它放进文档里合适的位置，第二行换成一句话：它展示什么、最重要的结论是什么。读文档的人在运行不了的地方只看到这一句，你以后读文档时也只读这一句。
3. 改已有的小程序：先 `cheese_app_get` 读代码，改好后 `cheese_app_save` 带上 `app`（块里 `:::app` 后面那串短号）。文档里块的版本号会一起改掉。

## 代码里写什么

`code` 是一段 HTML：标签加 `<script>`。只写内容，不写 `<html>`、`<head>`、`<body>`，不写页面的配色、字体和背景，这些外壳都有。标签先放好，脚本再按顺序运行，脚本里可以直接用：

- `kit`：组件，见下表；
- `echarts`：完整的 ECharts，一般用 `kit.chart` 就够；
- `katex`：公式，一般用 `kit.math` 就够。

不能联网，不能加载别的文件或库，也不能用 `localStorage`、弹窗和表单提交。图片只能用 `data:` 地址。

## kit

| 写法 | 得到什么 |
| --- | --- |
| `kit.h(标签, 属性, ...子元素)` | 一个元素；属性里 `onclick` 这类函数是事件，`style` 可以是对象 |
| `kit.row(...子元素)` | 横排一行，窄了自动换行 |
| `kit.slider({ label, min, max, step, value, format, onInput })` | 带名称和当前值的滑块；`format` 把数变成显示的文字 |
| `kit.select([[值, 文字], …], 当前值, onChange)` | 下拉框 |
| `kit.segmented([[值, 文字], …], 当前值, onChange)` | 分段按钮，两到四个选项时用 |
| `kit.input({ value, type, label, onInput })` | 输入框，`type` 是 `number` 或 `text` |
| `kit.field(名称, 控件)` | 给下拉框、输入框配上名称 |
| `kit.button(文字, onClick, { primary })` | 按钮 |
| `kit.stat(名称, 数值, 变化)` | 指标卡，几张放进一个 `kit.row` |
| `kit.chip(种类, 文字)` | 状态标签，种类是 `ok`（✓）、`no`（✗）、`warn`（!） |
| `kit.table(表头, 行, { numeric })` | 表格；`numeric` 是要右对齐的列号 |
| `kit.chart(ECharts 配置, { height })` | 图表，配色跟着文档；返回的元素用 `update(新配置)` 重画 |
| `kit.math(LaTeX, { display })` | 公式 |
| `kit.color(名称)` | 文档配色里的颜色，如 `text`、`muted`、`line`、`ok`、`danger`、`chart-1` 到 `chart-8` |
| `kit.mode()`、`kit.on('mode', fn)` | 现在是内嵌（`inline`）还是全屏（`full`） |
| `kit.on('theme', fn)` | 读者换了浅色或深色；kit 的组件和图表自己会跟着变，自己画的颜色在这里重画 |

颜色只用 `kit.color` 取，不写死：同一个小程序要在浅色和深色下都看得清。

## 尺寸

宽度跟着文档那一栏，手机上三百多像素，桌面上六七百像素。按窄的写，并排的东西放进 `kit.row`。

高度由内容撑开。内嵌时不要用 `vh`、`height: 100%` 或整屏的固定高度：外壳按内容的高度调整外框，内容又跟着外框变高，高度会一直长，交稿会被拒。图表给一个固定的 `height`，200 到 320 之间。

## 例子

```html
<div id="controls"></div>
<div id="result"></div>
<script>
  const jobs = 160
  let workers = 4
  const chart = kit.chart(
    {
      xAxis: { type: 'category', data: [1, 2, 4, 8, 16] },
      yAxis: { type: 'value', name: '排队（秒）' },
      series: [{ type: 'line', data: [1, 2, 4, 8, 16].map((n) => Math.round(jobs / n)) }],
    },
    { height: 220 }
  )
  function draw() {
    const wait = Math.round(jobs / workers)
    const cost = (0.3 * workers).toFixed(1)
    document.getElementById('result').replaceChildren(
      kit.row(kit.stat('平均排队', `${wait} 秒`), kit.stat('每小时花费', `$${cost}`)),
      workers > 8 ? kit.chip('warn', '再加并发，排队几乎不再缩短') : kit.chip('ok', '还有缩短的空间')
    )
  }
  document.getElementById('controls').append(
    kit.slider({ label: '并发数', min: 1, max: 16, value: workers, onInput: (v) => { workers = v; draw() } }),
    chart
  )
  draw()
</script>
```
