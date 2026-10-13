---
name: doc-apps
title: 小程序的写法
description: 文档里小程序的代码怎么写、样子怎么定。随 cheese-docs 技能发出，在里面是 references/apps.md。
---

# 小程序的写法

小程序是嵌在文档里、能运行的一小段程序。用不用它只看一点：读者自己动一动，或者看它动起来，是不是比读文字、表格、图表更快明白。是，就做成小程序，讲什么都可以；正文、表格、图表块、时间线说得清的，不用它。一份文档里一般不超过两个。

## 怎么交

1. 用 `cheese_app_save` 交代码（`code`）和标题（`title`）。平台先在浏览器里运行一遍，手机宽和桌面宽、浅色和深色各一次：报错、什么都没画出来、高度一直在长，就不存，返回原因。
2. 新建时返回一个块。把它放进文档里合适的位置，第二行换成一句话：它展示什么、最重要的结论是什么。读者在运行不了的地方只看到这一句，你以后读文档时也只读这一句。
3. 改已有的小程序：先 `cheese_app_get` 读代码，改好后 `cheese_app_save` 带上 `app`（块里 `:::app` 后面那串短号）。文档里块的版本号会一起改掉。

## 代码是一个 Vue 组件

`code` 是一个 Vue 单文件组件：`<script setup>`、`<template>`，需要时加 `<style>`（不支持 `scoped`，类名起得具体一些）。

- 只能 `import { ref, computed, watch, onMounted, onUnmounted } from 'vue'` 这类 Vue 自己的东西，别的库都没有，也不能联网、不能用 `localStorage`、`alert` 和表单提交。
- 宽度跟着文档那一栏：手机上三百多像素，桌面上六七百像素。按窄的写，用 flex 或 grid 排，窄了能换行或缩小。
- 高度由内容撑开。不要用 `vh`、`height: 100%` 或固定的整屏高度：平台按内容的高度调整外框，内容又跟着外框变高，高度会一直长，交稿会被拒。

## 用在哪里

读者在小程序里做的事，大致是这几种：

- **看懂原理**：一个机制、一个过程怎么运作。把里面的东西画出来，读者一步步播放或拖动，看它们怎么动、怎么变。
- **试一试**：改一个或几个条件，看结果怎么变。结果画成能一眼比较的形状，并画出要紧的界线（上限、临界点、目标），读者拖动时能看见越过它的那一刻。
- **比一比**：几个方案或选项摆在一起，差别直接看得出，所选的那一个突出显示。
- **走一遍**：一个方案、计划或一件事按时间推演，读者看见每一步发生了什么，前一步怎么影响后一步。
- **练一练**：读者自己动手做一道题、完成一个操作，马上知道对不对。做错了指出错在哪、为什么，不只说一句「错了」。

## 怎么做

按先后，三条：

1. **能交互**：读者能改点什么，拖、点、填、播放、作答，画面跟着变。
2. **直观**：把文档讲的那个东西本身画出来，数字只是辅助。做完自查：把画面上的数字和文字遮住，还看得出发生了什么吗？看不出来，就只是把数字换了个排法；几张指标卡、一张通用图表加一张表，读者得到的和读正文差不多。
3. **动起来**：变化用过渡动画表现，东西移动、出现、消失、换颜色，读者看见它是怎么变过去的，而不是前后两幅图。

怎么画：

- 排成格子、列表、条形的东西，用 HTML 加 CSS（flex、grid），文字保持文档的字号，窄屏上也清楚。
- 形状、连线、流向，用内联 SVG：写 `viewBox` 加 `width="100%"`，图会随栏宽缩放，里面的字也跟着缩，所以 SVG 里少放字，标签放在 SVG 外面的 HTML 里。
- 真正的数据曲线（随时间的变化、分布、趋势）用 `<c-chart>`。
- 颜色、字号、动效都用下面设计规范里的变量。

## 设计规范

小程序嵌在文档里，要像文档的一部分。外框、背景、字体和深浅色由平台负责；颜色、字号、圆角、动效从下面的变量取，深色模式会自动跟着换。在这个范围里，画什么、怎么排，由你决定。

颜色写成 `var(--名字)`，一个也不写死：`#2863ab`、`red`、`white` 这类颜色在深色模式下会看不见或刺眼。

| 用在 | 变量 |
| --- | --- |
| 标题、最重要的数 | `--ink` |
| 正文 | `--text` |
| 次要的文字、单位、坐标 | `--muted`，更淡的 `--faint` |
| 线、边框 | `--line`，深一点的 `--line-2` |
| 底色 | `--surface` 是页面本身；`--fill`、`--fill-2` 是浅底，用来分区，或表示空着的格子 |
| 区分几类东西（几种请求、几个方案、几条线） | `--chart-1` 到 `--chart-8`，按顺序用 |
| 好、注意、坏 | 图形用 `--ok` `--warn` `--danger`，文字用 `--ok-ink` `--warn-ink` `--danger-ink`，浅底用 `--ok-wash` `--warn-wash` `--danger-wash` |
| 当前这一个、读者正在看的 | 图形用 `--accent`，浅底用 `--accent-wash`，文字用 `--accent-ink`；一个小程序里只用在一处 |

- **字号**：正文 14px；次要的字 13px，不再更小；小标题 15px；要突出的数 18px 或 23px。数字加 `font-variant-numeric: tabular-nums`，变化时不会左右跳。等宽字体是 `var(--font-mono)`。
- **间距**：取 4 的倍数，常用 4、8、12、16、24。
- **圆角**：`var(--radius-sm)` 用于小格子和按钮，`var(--radius-md)` 用于一块区域，`var(--radius-lg)` 用于大面板。
- **动效**：动画用来表现变化。时长用 `var(--dur-quick)`（小的变化）、`var(--dur-base)`（一般）、`var(--dur-slow)`（东西移动较远），曲线用 `var(--ease-standard)`。只过渡写明的属性（`transform`、`opacity`、`background-color` 等），不写 `transition: all`。不做一直循环的装饰动画。
- **不做**：阴影；渐变、横幅、大标题这类装饰；把整个小程序再包进一张卡片（外框平台已经画了）；解释性的大段文字（正文已经说了）。

## 组件

控件和几样常用的展示件由平台提供，样子已经和文档一致。标签名以 `c-` 开头，直接写在 `<template>` 里。

输入控件和原生输入框一样用：直接 `v-model`；也可以 `:value` 加 `@input`，新值在 `$event.target.value`。滑块和数字框的值是数字。数字、数组和对象都用绑定传：`:min="0"`、`:options="…"`、`:option="…"`。

| 标签 | 属性 | 什么时候用 |
| --- | --- | --- |
| `<c-slider>` 滑块 | `label` `min` `max` `step` `unit`（数值后的单位，如 `"%"`）`:format`（函数，自己决定数值怎么显示）`disabled` | 拖一个参数 |
| `<c-number>` 数字输入框 | `label` `min` `max` `step` `unit` `disabled` | 填一个数；离开输入框时超出范围的值会被拉回 |
| `<c-select>` 下拉框 | `label` `:options` `disabled` | 五个以上选项里选一个 |
| `<c-segmented>` 分段按钮 | `label` `:options` `disabled` | 两到四个选项里选一个 |
| `<c-switch>` 开关 | `label` `disabled`；值是 `true` / `false` | 开或关一样东西 |
| `<c-player>` 分步播放 | `:count`（一共几步）`:labels`（每一步的名字，如 `["第 1 秒", …]`）`:interval`（自动播放每步停几毫秒，默认 1000）`loop`；值是当前第几步，**从 0 开始** | 一步一步播放一个过程：上一步、下一步、播放和暂停都由它管，不要自己写定时器 |
| `<c-stat>` 指标卡 | `label` `value` `delta`（下面一行小字）`tone`（`good` 绿、`bad` 红） | 图旁边点出一两个关键数字，几张放进一个 `<c-row>` |
| `<c-chip>` 状态标签，文字写在标签中间 | `kind`：`ok`（✓）、`no`（✗）、`warn`（!） | 点出结论 |
| `<c-chart>` 图表 | `:option`（一份 ECharts 配置对象）`:height`（像素，200 到 320） | 真正的数据曲线 |
| `<c-math>` 公式 | `tex`（LaTeX）`display`（单独成行） | 写计算方法 |
| `<c-row>` 横排一行 | `:gap`（像素）`align`（`start` `center` `end` `stretch`，默认 `end`） | 并排放几样东西，窄了自动换行 |

`options` 可以是 `["甲", "乙"]`，也可以是 `[["a", "方案 A"], ["b", "方案 B"]]`（值, 显示的文字）。

普通的 `<table>`、`<button>`、`<p>`、`<small>` 已经是文档的样子，直接用：`<tr class="active">` 高亮一行，`<td class="num">` 数字右对齐，`<button class="primary">` 主要按钮。

图表的配色、字体、坐标轴颜色由平台给，配置里不写颜色。图例默认放在图上方，标线的文字默认放在图里面。用 `markPoint` 标出当前选中的点；两条线的当前点挨得很近时，只标最重要的那一条，标签才不会互相遮挡。

## 写好的标准

- 读者不读正文，只看画面、动一动，也能明白它讲的东西和结论。
- 数值照文档给的数据算，不另外假设文档没给的条件；算出来的数保留合适的小数位。
- 画面上的字只有标签、单位和一句结论；练一练给的反馈，可以多一句为什么。

## 例子

二分查找一步步缩小范围：每一步看中间那一格，丢掉不可能的一半。读者看见范围在缩小、指针在移动，比读一段描述更直接。

```vue
<script setup>
import { ref, computed } from 'vue'

const list = [3, 8, 12, 17, 21, 26, 30, 34, 39, 45, 52, 58]
const target = 45
const steps = []
for (let lo = 0, hi = list.length - 1; lo <= hi; ) {
  const mid = Math.floor((lo + hi) / 2)
  steps.push({ lo, hi, mid })
  if (list[mid] === target) break
  if (list[mid] < target) lo = mid + 1
  else hi = mid - 1
}
const step = ref(0)
const now = computed(() => steps[step.value])
const seen = computed(() => list[now.value.mid])
</script>

<template>
  <c-player :count="steps.length" :labels="steps.map((_, i) => `第 ${i + 1} 次比较`)" :interval="1200" v-model="step" />
  <div class="bs-cells">
    <div
      v-for="(v, i) in list"
      :key="i"
      class="bs-cell"
      :class="{ 'bs-out': i < now.lo || i > now.hi, 'bs-mid': i === now.mid, 'bs-hit': i === now.mid && v === target }"
    >
      {{ v }}
    </div>
  </div>
  <div class="bs-cells">
    <div class="bs-pointer" :style="{ transform: `translateX(calc(${now.mid} * (100% + 4px)))` }">▲</div>
  </div>
  <p v-if="seen === target"><c-chip kind="ok">找到 {{ target }}，比较了 {{ step + 1 }} 次</c-chip></p>
  <p v-else>
    中间是 {{ seen }}，比 {{ target }} {{ seen < target ? '小，丢掉左边一半' : '大，丢掉右边一半' }}
  </p>
</template>

<style>
.bs-cells { display: grid; grid-template-columns: repeat(12, 1fr); gap: 4px; margin-top: 12px; }
.bs-cell {
  padding: 8px 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  text-align: center;
  font-variant-numeric: tabular-nums;
  transition: opacity var(--dur-base) var(--ease-standard), background-color var(--dur-base) var(--ease-standard);
}
.bs-out { opacity: 0.3; }
.bs-mid { border-color: var(--accent); background: var(--accent-wash); color: var(--ink); }
.bs-hit { border-color: var(--ok); background: var(--ok-wash); color: var(--ok-ink); }
.bs-pointer { color: var(--accent); text-align: center; transition: transform var(--dur-slow) var(--ease-standard); }
</style>
```
