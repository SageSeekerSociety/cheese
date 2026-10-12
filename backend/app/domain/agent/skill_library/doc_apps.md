---
name: doc-apps
title: 小程序的写法
description: 文档里小程序的代码怎么写。随 cheese-docs 技能发出，在里面是 references/apps.md。
---

# 小程序的写法

读者要自己动手才看得懂时才用小程序：拖动一个参数看结果怎么变、一步一步播放一个过程、在几种方案之间切换对比、填数试算。能用表格、图表块、时间线说清楚的，不用它。一份文档里一般不超过两个。

## 怎么交

1. 用 `cheese_app_save` 交代码（`code`）和标题（`title`）。平台先在浏览器里运行一遍，手机宽和桌面宽、浅色和深色各一次：报错、什么都没画出来、高度一直在长，就不存，返回原因。
2. 新建时返回一个块。把它放进文档里合适的位置，第二行换成一句话：它展示什么、最重要的结论是什么。读者在运行不了的地方只看到这一句，你以后读文档时也只读这一句。
3. 改已有的小程序：先 `cheese_app_get` 读代码，改好后 `cheese_app_save` 带上 `app`（块里 `:::app` 后面那串短号）。文档里块的版本号会一起改掉。

## 代码是一个 Vue 组件

`code` 是一个 Vue 单文件组件：`<script setup>` 加 `<template>`。

- 只能 `import { ref, computed, watch, onMounted, onUnmounted } from 'vue'` 这类 Vue 自己的东西，别的库都没有，也不能联网、不能用 `localStorage`、`alert` 和表单提交。
- 页面骨架、配色、字体、深浅色都由平台负责。不写 `<style>` 改字体、背景和颜色，不做大标题、色块这类装饰；排版用 `<c-row>`。确实要写样式时只写间距和排版，颜色只用下面这些变量：`var(--text)` `var(--muted)` `var(--line)` `var(--fill)` `var(--ok-ink)` `var(--danger-ink)` `var(--chart-1)` 到 `var(--chart-8)`。
- 宽度跟着文档那一栏：手机上三百多像素，桌面上六七百像素。按窄的写，并排的东西放进 `<c-row>`，窄了会自动换行。
- 高度由内容撑开。不要用 `vh`、`height: 100%` 或固定的整屏高度：平台按内容的高度调整外框，内容又跟着外框变高，高度会一直长，交稿会被拒。

## 组件

平台提供下面这些组件，标签名以 `c-` 开头，直接写在 `<template>` 里。

输入控件和原生输入框一样用：直接 `v-model`；也可以 `:value` 加 `@input`，新值在 `$event.target.value`。滑块和数字框的值是数字。数字、数组和对象都用绑定传：`:min="0"`、`:options="…"`、`:option="…"`。

| 标签 | 属性 | 什么时候用 |
| --- | --- | --- |
| `<c-slider>` 滑块 | `label` `min` `max` `step` `unit`（数值后的单位，如 `"%"`）`:format`（函数，自己决定数值怎么显示）`disabled` | 拖一个参数 |
| `<c-number>` 数字输入框 | `label` `min` `max` `step` `unit` `disabled` | 填一个数；离开输入框时超出范围的值会被拉回 |
| `<c-select>` 下拉框 | `label` `:options` `disabled` | 五个以上选项里选一个 |
| `<c-segmented>` 分段按钮 | `label` `:options` `disabled` | 两到四个选项里选一个 |
| `<c-switch>` 开关 | `label` `disabled`；值是 `true` / `false` | 开或关一样东西 |
| `<c-player>` 分步播放 | `:count`（一共几步）`:labels`（每一步的名字，如 `["第 1 秒", …]`）`:interval`（自动播放每步停几毫秒，默认 1000）`loop`；值是当前第几步，**从 0 开始** | 一步一步播放一个过程：上一步、下一步、播放和暂停都由它管，不要自己写定时器 |
| `<c-stat>` 指标卡 | `label` `value` `delta`（下面一行小字）`tone`（`good` 绿、`bad` 红） | 突出几个关键数字，几张放进一个 `<c-row>` |
| `<c-chip>` 状态标签，文字写在标签中间 | `kind`：`ok`（✓）、`no`（✗）、`warn`（!） | 点出结论 |
| `<c-chart>` 图表 | `:option`（一份 ECharts 配置对象）`:height`（像素，200 到 320） | 画图 |
| `<c-math>` 公式 | `tex`（LaTeX）`display`（单独成行） | 写计算方法 |
| `<c-row>` 横排一行 | `:gap`（像素）`align`（`start` `center` `end` `stretch`，默认 `end`） | 并排放几样东西 |

`options` 可以是 `["甲", "乙"]`，也可以是 `[["a", "方案 A"], ["b", "方案 B"]]`（值, 显示的文字）。

普通的 `<table>`、`<button>`、`<p>`、`<small>` 已经是文档的样子，直接用：`<tr class="active">` 高亮一行，`<td class="num">` 数字右对齐，`<button class="primary">` 主要按钮。

图表的配色、字体、坐标轴颜色由平台给，配置里不写颜色。图例默认放在图上方，标线的文字默认放在图里面。用 `markPoint` 标出当前选中的点；两条线的当前点挨得很近时，只标最重要的那一条，标签才不会互相遮挡。

## 写好的标准

- 先放要动的控件，再放结果，最重要的结论用指标卡或状态标签点出来。
- 控件一动，图表和数字都跟着变；图表上标出当前选中的点或那一项。
- 数值照文档给的数据算，不另外假设文档没给的条件；算出来的数保留合适的小数位。
- 不写解释性的大段文字，文档正文已经说了；小程序里只放必要的标题和单位。

## 例子

```vue
<script setup>
import { ref, computed } from 'vue'

const counts = [1, 2, 4, 8, 16]
const jobs = 160
const workers = ref(4)
const wait = computed(() => Math.round(jobs / workers.value))
const option = computed(() => ({
  xAxis: { type: 'category', name: '并发数', data: counts.map(String) },
  yAxis: { type: 'value', name: '排队（秒）' },
  series: [
    {
      type: 'line',
      data: counts.map((n) => Math.round(jobs / n)),
      markPoint: { data: [{ coord: [String(workers.value), wait.value] }] },
    },
  ],
}))
</script>

<template>
  <c-segmented label="并发数" :options="counts" v-model="workers" />
  <c-row>
    <c-stat label="平均排队" :value="`${wait} 秒`" />
    <c-stat label="每小时花费" :value="`$${(0.3 * workers).toFixed(1)}`" />
  </c-row>
  <c-chip v-if="workers > 8" kind="warn">再加并发，排队几乎不再缩短</c-chip>
  <c-chip v-else kind="ok">还有缩短的空间</c-chip>
  <c-chart :option="option" :height="220" />
</template>
```
