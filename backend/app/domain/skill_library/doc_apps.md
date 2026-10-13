---
name: doc-apps
title: 小程序的写法
description: 文档中小程序的代码写法和设计规范。随 cheese-docs 技能下发，路径为 references/apps.md。
---

# 小程序的写法

小程序是嵌在文档中、可以运行的一段程序。

## 什么时候用

文档在讲解一件事，读者需要看到它如何变化、或者亲手试过才能理解时，用小程序。常见的有以下几类：

- **理解原理**：一个机制如何运作。图只能画出某一时刻；小程序让读者逐步播放，看清各部分如何衔接。
- **改变条件**：结果如何随条件变化。正文只能举几个取值；小程序让读者自己调整，看出其中的规律。
- **比较方案**：哪个方案更好，取决于具体条件。表格只能列出固定条件下的结果；小程序让读者代入自己的条件比较。
- **推演过程**：一件事如何随时间发展。时间线只列出结果；小程序让读者看到前一步如何影响后一步。
- **动手组合**：读者自己排列、搭配或组合几样东西，看这样组合会产生什么结果。正文只能给出一种现成的组合；读者亲手试过，才能体会各部分如何相互制约。这类没有标准答案，画面展示结果，不判对错。
- **练习**：读者做一道有标准答案的题，立即得到对错判断和原因。练习是对读者的考查，只在用户要求出题，或者文档本身是讲义、作业这类教学材料时才做，不主动给文档添加题目。

讲义、预习材料、原理说明、方案分析这类讲解性的文档中，遇到前五类内容，默认用小程序。结论固定、读者没有可以调整的东西时，不用。

## 提交

1. 用 `cheese_app_save` 提交代码（`code`）和标题（`title`）。平台先在浏览器中以手机和桌面两种宽度、浅色和深色两种主题各运行一次。出现以下情况时不保存，并返回原因：运行报错，页面没有内容，高度持续增长，自查（`console.assert`）未通过，读者操作的控件没有绑定到变量。
2. 新建时返回一个块。把它放到文档中合适的位置，并把块的第二行改写为一句说明：小程序展示什么，最重要的结论是什么。在无法运行小程序的地方，读者只能看到这句说明；你以后读这份文档时，也只会读到这一句。
3. 修改已有的小程序：先用 `cheese_app_get` 读取代码，改好后用 `cheese_app_save` 提交，并带上 `app`（块中 `:::app` 后面的短号）。文档中块的版本号会同步更新。

## 代码是一个 Vue 组件

`code` 是一个 Vue 单文件组件：`<script setup>`、`<template>`，需要时加 `<style>`（不支持 `scoped`，类名要起得具体）。

- 只能从 `vue` 和平台提供的 `@platform/motion`、`@platform/sim`、`@platform/random`、`@platform/theme`（见下文「过渡」「模拟与随机数」「在脚本中取颜色」）导入，例如 `import { ref, computed, watch, onMounted, onUnmounted } from 'vue'`。没有其他库，不能联网，不能使用 `localStorage`、`alert` 和表单提交。
- 宽度与文档的栏宽一致：手机上三百多像素，桌面上六七百像素。按窄屏设计，用 flex 或 grid 排版，宽度不足时换行或缩小。
- 高度由内容决定。不要使用 `vh`、`height: 100%` 或固定的整屏高度：平台按内容高度调整外框，内容又随外框变高，高度会持续增长，保存会被拒绝。

## 在文档中的位置

小程序是文档中的一个块，位于正文之间。块的顶部由平台显示标题和说明，旁边有全屏按钮。

- 小程序内不写标题，也不写解释性的段落：原理由正文讲解，小程序只放图形和供读者操作的控件。
- 高度控制在 400 至 550 像素，约半屏到一屏，读者看完后能很快回到正文。内容较多时，用分步播放或切换来收纳，不要纵向堆叠。
- 在无法运行的地方（读者无权查看所属文档、文档导出为文件），只显示块顶部的说明，因此这句说明要把结论写完整。

## 设计要求

编写代码前，先确定画面：画的是什么对象，读者操作时哪些部分随之变化。如果设想中的画面只有几个数字和一张表格，说明还没有找到要画的对象。

然后依次满足以下三条：

1. **可交互**：读者可以拖动、点击、输入、播放或作答，画面随之变化。控件只提供实际情况中可以调整的一两个量，并限定范围。交互方式与实际操作一致：本来就是拿起放下、排列顺序、两两配对的对象，让读者直接拖动；调整连续的数值用滑块；点击即可完成的操作不改成拖拽。
2. **直观**：画出文档所讲的对象本身，数字只作辅助。完成后自查：遮住画面上的数字和文字，读者能否仍然看出发生了什么。如果不能，画面只是把数字换了一种排列；几张指标卡、一张通用图表加一张表格，读者从中得到的与阅读正文无异。
3. **动起来**：让所讲的对象本身动起来，而不只是展示几个静止的状态。过程可以播放，机制中的各部分在运转，变化连续地展现出来，读者能看到它如何一步步变成后来的样子，例如行星沿轨道运行、队伍逐渐变长。随时间发展的过程用 `simulate` 推进（见下文「模拟与随机数」）。内容本身不随时间或过程变化时（例如静态方案的比较），这一条不适用。

初始状态就要体现要点：默认值选最能说明问题的一组。多数读者不会操作小程序。

各类小程序的要点：

- **改变条件**：结果画成便于比较的图形，并画出关键的界线（上限、临界点、目标），读者调整时能看到越过界线的那一刻。
- **比较方案**：几个方案并列展示，差别一目了然，选中的一项突出显示。
- **推演过程**：每一步发生了什么，以及它如何影响下一步，都画在图上。
- **动手组合**：读者每改动一处，画面立即显示新组合的结果；出现冲突或不满足条件的地方，在图上直接标出。
- **练习**：所有正确答案都判为正确（相等的分数、写法不同的等价式子）；答错时指出错在哪一步、原因是什么，而不只是提示「错误」。

画面要简洁：

- 一个小程序只讲一件事，以一幅主图为中心。
- 数字标在它所描述的对象旁边。图上已有的数字，不在指标卡或表格中重复；指标卡只放图上放不下、又属于结论的一两个数。
- 结论只表述一次：一句话或一个状态标签，二者选一。
- 除标签和单位外，画面上的文字不超过一两行。公式和计算过程，只在文档要求读者核对时给出。

绘制方法：

- 网格、列表、条形这类排列，用 HTML 和 CSS（flex、grid）实现，文字保持文档字号，在窄屏上依然清晰。
- 形状、连线和流向用内联 SVG，设置 `viewBox` 和 `width="100%"`，图形随栏宽缩放。SVG 中的文字也会随之缩小，因此少在 SVG 内放文字：标签和数字放在紧邻图形的 HTML 中（叠放在图上或排在旁边），字号就不会变小。
- 有坐标的图（函数、曲线、界线、面积、可拖动的点和向量）用 `<c-plane>`：其中的元素直接按坐标书写，坐标换算、坐标轴、拖动和过渡由它处理。
- 读者需要拖入格子、排列顺序、配对或归类时，用 `<c-board>`：格子和块的外观由你编写，拖动、点选、键盘操作和移动动画由它处理。
- `<c-chart>` 只用于呈现较长的实测或给定数据序列，例如几十个时间点的走势、一组样本的分布。讲解机制、模型或过程时，画出对象本身（SVG 或 `<c-plane>`），不用通用图表代替。
- 坐标范围保持固定，不随数据缩放，界线始终留在画面内；图上所有位置用同一个换算函数计算，不逐处手算。
- 颜色、字号和动效使用下文设计规范中的变量。

## 设计规范

小程序嵌在文档中，应当看起来是文档的一部分。外框、背景、字体和深浅色由平台负责；颜色、字号、圆角和动效取用下列变量，深色模式下会自动切换。在此范围内，画什么、如何布局，由你决定。

颜色一律写成 `var(--名字)`，不写固定值：`#2863ab`、`red`、`white` 这类颜色在深色模式下会看不清或过于刺眼。

| 用途 | 变量 |
| --- | --- |
| 标题、最重要的数 | `--ink` |
| 正文 | `--text` |
| 次要文字、单位、坐标 | `--muted`，更淡的 `--faint` |
| 线、边框 | `--line`，较深的 `--line-2` |
| 底色 | `--surface` 是页面本身；`--fill`、`--fill-2` 是浅色底，用于分区或表示空格子 |
| 区分几类对象（几种请求、几个方案、几条线） | `--chart-1` 到 `--chart-8`，按顺序使用 |
| 良好、注意、不良 | 图形用 `--ok` `--warn` `--danger`，文字用 `--ok-ink` `--warn-ink` `--danger-ink`，浅色底用 `--ok-wash` `--warn-wash` `--danger-wash` |
| 当前项、读者正在查看的对象 | 图形用 `--accent`，浅色底用 `--accent-wash`，文字用 `--accent-ink`；一个小程序中只用于一处 |

- **字号**：正文 14px；次要文字 13px，不再更小；小标题 15px；需要突出的数 18px 或 23px。数字加 `font-variant-numeric: tabular-nums`，数值变化时不会左右跳动。等宽字体是 `var(--font-mono)`。
- **间距**：取 4 的倍数，常用 4、8、12、16、24。
- **圆角**：`var(--radius-sm)` 用于小格子和按钮，`var(--radius-md)` 用于一块区域，`var(--radius-lg)` 用于大面板。
- **动效**：动画用于表现变化。时长用 `var(--dur-quick)`（小幅变化）、`var(--dur-base)`（一般变化）、`var(--dur-slow)`（移动距离较远），缓动曲线用 `var(--ease-standard)`。只为指定的属性设置过渡（`transform`、`opacity`、`background-color` 等），不写 `transition: all`。不做持续循环的装饰性动画。
- **不使用**：阴影；渐变、横幅、大标题等装饰；把整个小程序再包进一张卡片（外框由平台绘制）；解释性的大段文字（正文已经讲过）。

## 组件

控件和几种常用的展示组件由平台提供，样式与文档一致。标签名以 `c-` 开头，直接写在 `<template>` 中。

输入控件的用法与原生输入框相同：使用 `v-model`，或者用 `:value` 加 `@input`，新值在 `$event.target.value` 中。`v-model` 绑定 `ref`，不绑定 `computed`：`computed` 无法写入，读者操作后值不会改变。滑块和数字输入框的值是数字。数字、数组和对象都通过绑定传入：`:min="0"`、`:options="…"`、`:option="…"`。

| 标签 | 属性 | 用途 |
| --- | --- | --- |
| `<c-slider>` 滑块 | `label` `min` `max` `step` `unit`（数值后的单位，如 `"%"`）`:format`（函数，自定义数值的显示方式）`disabled` | 调整一个参数 |
| `<c-number>` 数字输入框 | `label` `min` `max` `step` `unit` `disabled` | 输入一个数；失去焦点时，超出范围的值会被修正到范围内 |
| `<c-select>` 下拉框 | `label` `:options` `disabled` | 从五个以上选项中选一个 |
| `<c-segmented>` 分段按钮 | `label` `:options` `disabled` | 从两到四个选项中选一个 |
| `<c-switch>` 开关 | `label` `disabled`；值为 `true` 或 `false` | 开启或关闭一项 |
| `<c-player>` 分步播放 | `:count`（总步数）`:labels`（每一步的名称，如 `["第 1 秒", …]`）`:interval`（自动播放时每步停留的毫秒数，默认 1000）`loop`；值是当前步的序号，**从 0 开始** | 逐步播放一个过程：上一步、下一步、播放和暂停都由它处理，无需自己编写定时器 |
| `<c-stat>` 指标卡 | `label` `value` `delta`（下方的一行小字）`tone`（`good` 绿色、`bad` 红色） | 图上放不下、又属于结论的一两个数，几张放进一个 `<c-row>` |
| `<c-chip>` 状态标签，文字写在标签内 | `kind`：`ok`（✓）、`no`（✗）、`warn`（!） | 标明结论 |
| `<c-chart>` 图表 | `:option`（一份 ECharts 配置对象）`:height`（像素，200 至 320） | 较长的实测或给定数据序列的走势、分布 |
| `<c-math>` 公式 | `tex`（LaTeX）`display`（单独成行） | 展示计算公式 |
| `<c-row>` 横向排列 | `:gap`（像素）`align`（`start` `center` `end` `stretch`，默认 `end`） | 横向排列几项内容，宽度不足时自动换行 |
| `<c-plane>` 坐标平面 | `:x` `:y`（坐标范围，`[最小值, 最大值]`）`:height`（像素，默认 240）`x-label` `y-label` `:grid`（网格线，默认显示） | 容纳下面四种元素并绘制坐标轴 |
| `<c-point>` 点（放在 `c-plane` 内） | `v-model`（`[x, y]`）`drag`（`xy`、`x`、`y`：读者可拖动的方向；不写则不能拖动）`:snap`（吸附间隔）`:on`（函数 x → y，点只能沿这条曲线移动）`:from`（`[x, y]`，从此处向点画一个箭头，即向量）`tone` `label` | 可拖动的点、向量、标出的位置 |
| `<c-curve>` 曲线（放在 `c-plane` 内） | `:f`（函数 x → y）`:from` `:to`（x 的范围，默认为整个平面）`tone` `dashed` `label` | 函数图像 |
| `<c-line>` 直线（放在 `c-plane` 内） | `:x`（竖线）或 `:y`（横线）或 `:from` `:to`（线段两端）`tone` `dashed` `label` | 上限、临界值、切线、连线 |
| `<c-area>` 面积（放在 `c-plane` 内） | `:points`（多边形的顶点）或 `:f` `:from` `:to`（曲线与 `:base` 之间的面积，`:base` 默认为 0）`tone` `label` | 区间、概率、税收等面积 |
| `<c-board>` 拖放板 | `v-model`（`{ 区名: [块名, …] }`，每个区中依次有哪些块） | 拖入格子、排列顺序、配对、归类 |
| `<c-zone>` 区（放在 `c-board` 内） | `name` `:max`（最多容纳几块；已满时再拖入一块，会与原有的块交换） | 可以放置块的位置 |
| `<c-piece>` 块（放在 `c-zone` 内） | `name` `fixed`（不能拖动） | 读者拖动的对象 |

`options` 可以写成 `["甲", "乙"]`，也可以写成 `[["a", "方案 A"], ["b", "方案 B"]]`（值和显示的文字）。

普通的 `<table>`、`<button>`、`<p>`、`<small>` 已经是文档的样式，可以直接使用：`<tr class="active">` 高亮一行，`<td class="num">` 让数字右对齐，`<button class="primary">` 表示主要按钮。

`tone` 表示颜色：`1` 到 `8` 对应 `--chart-1` 到 `--chart-8`，此外还有 `ok` `warn` `danger` `muted`。读者正在拖动的点由平台以强调色标出，无需另行标注。

坐标平面中的点、线和面积在数值改变时平滑移动到新位置，拖动时跟随指针；平面宽度随栏宽变化，字号不变。拖放板的值决定每个块的位置：在每个区中用 `v-for` 列出值中属于该区的块；读者移动一个块后，值随之更新，块从原位置平滑移到新位置。区和块默认显示为虚线框和小卡片，可以用自定义类名改为格子、卡槽、链条等样式。读者也可以先点选一个块，再点选一个区来放置；使用键盘时，按 Enter 选取，再按 Enter 放下。

图表的配色、字体和坐标轴颜色由平台提供，配置中不写颜色。图例默认位于图的上方，标线文字默认位于图内。用 `markPoint` 标出当前选中的点；两条线的当前点相距很近时，只标注最重要的一条，避免标签相互遮挡。

## 过渡

读者拖动或拨动滑块时，画面直接跟随操作。切换步骤、切换选项、提交答案这类一次性的变化，用过渡表现变化的去向，不要让画面直接跳到新状态。有以下两种方式：

- **数值过渡**：从 `@platform/motion` 导入 `tween`。`tween(值)` 返回这个值的一个副本：值改变时，副本在约 0.2 秒内平滑过渡到新值。值可以是数字，也可以是由数字组成的数组或对象；传入 `ref`、`computed` 或一个返回值的函数都可以。绘图时用副本，SVG 的坐标、柱的高度、折线上的点、显示的数字就会平滑变化。读者连续拖动时，副本直接跟随，不再过渡。
- **出现、消失和换位**：用 Vue 的 `<Transition>` 或 `<TransitionGroup>`，`name` 写 `c-fade`（淡入淡出）或 `c-list`（带 `:key` 的列表项进出和换位）。

坐标平面中的点、线和面积，拖放板中的块，以及指标卡中的数字，已经自带过渡，无需另行处理。读者在系统中设置了减少动态效果时，以上过渡都会关闭。

## 模拟与随机数

推演一个随时间发展的过程（传染病传播、排队、轨道运动）时，用 `@platform/sim` 的 `simulate`，不要自己写定时器：

```js
const sim = simulate({
  init: () => ({ s: 990, i: 10, r: 0 }), // 初始状态
  step: (state, dt, t) => next,          // 由当前状态算出下一步的状态，可以直接修改 state 后返回它
  dt: 1,                                 // 每一步代表的时长，默认 1
  speed: 10,                             // 播放时每秒走几步，默认 10
  start: 30,                             // 打开时已经走过的步数，让初始画面就体现要点
})
```

返回的 `sim` 可以直接在模板中使用：`sim.state` 是当前状态，`sim.t` 是当前时刻，`sim.running` 表示是否在播放；`sim.play()`、`sim.pause()`、`sim.reset()` 分别是播放、暂停和回到开头，`sim.advance(n)` 一次走 `n` 步。播放和暂停用普通的 `<button>`。读者改了参数要从头开始时，写 `watch(参数, () => sim.reset())`。需要按微分方程精确推进时（轨道、摆），用同一模块的 `rk4(f, y, t, dt)`：`f(t, y)` 返回 `y` 各分量的变化率，`rk4` 返回走一步之后的 `y`。

需要随机数时，用 `@platform/random` 的 `random(种子)`，不用 `Math.random`。同一个种子每次产生同样的随机数，平台检查时拍下的画面就是读者看到的画面，`console.assert` 也能核对模拟的结果。它提供 `next()`（0 到 1 之间的数）、`uniform(a, b)`、`int(a, b)`、`normal(均值, 标准差)`、`exponential(速率)`、`poisson(均值)`、`pick(列表)`、`shuffle(列表)`。要让 `reset()` 后重现同样的过程，在 `init` 中重新创建它。

## 在脚本中取颜色

在 `<canvas>` 上作画时无法写 `var(--chart-1)`，用 `@platform/theme` 的 `color('chart-1')` 取得颜色；`color('1')` 到 `color('8')`、`color('ok')`、`color('muted')` 这类简写与 `tone` 一致。把作画的代码放进 `watchEffect`，读者切换深浅色时会自动重画。

## 质量标准

- 读者不读正文，只看画面并稍加操作，也能理解小程序讲的内容和结论。
- 数值按文档给出的数据计算，不另加文档没有给出的假设；计算结果保留合适的小数位数。
- 文档中明确给出的数值（算例的结果、表格中的数据），在代码中用 `console.assert` 核对，第二个参数写明核对的是哪个数，例如 `console.assert(Math.abs(price(0) - 24) < 1e-9, '不征税时均衡价应为 24')`。练习对每道题的每个正确答案都调用一次判题函数，确认判为正确。核对未通过时，平台不保存，并返回未通过的那一条。
- 同一个数在画面上只出现一次；文字只有标签、单位和一句结论，练习的反馈可以再加一句原因。

## 例子

二分查找逐步缩小范围：每一步检查中间的元素，排除不可能包含目标的一半。读者看到范围收缩、指针移动，比阅读文字描述更直接。

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
    中间是 {{ seen }}，比 {{ target }} {{ seen < target ? '小，排除左半部分' : '大，排除右半部分' }}
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

冒泡排序逐次交换相邻的两根柱子：读者点下一步，被交换的柱子滑到新位置，能看清每次交换的是哪两根。柱子的位置用 `tween` 过渡。

```vue
<script setup>
import { ref, computed } from 'vue'
import { tween } from '@platform/motion'

const start = [5, 2, 8, 3, 6]
const steps = [start.slice()]
for (let i = 0; i < start.length; i++) {
  for (let j = 0; j < start.length - 1 - i; j++) {
    const a = steps[steps.length - 1].slice()
    if (a[j] > a[j + 1]) {
      ;[a[j], a[j + 1]] = [a[j + 1], a[j]]
      steps.push(a)
    }
  }
}
const step = ref(0)
// 每根柱子此刻排在第几位；柱子按数值区分，交换时滑过去而不是重画
const slots = computed(() => start.map((v) => steps[step.value].indexOf(v)))
const x = tween(slots)
</script>

<template>
  <c-player :count="steps.length" :labels="steps.map((_, i) => (i ? `第 ${i} 次交换` : '开始'))" v-model="step" />
  <svg viewBox="0 0 250 100" width="100%">
    <rect
      v-for="(v, i) in start"
      :key="v"
      :x="x[i] * 50 + 5"
      :y="96 - v * 11"
      width="40"
      :height="v * 11"
      rx="4"
      fill="var(--chart-1)"
    />
  </svg>
</template>
```

窗口前的队伍：顾客每分钟随机到达，窗口每分钟最多服务 3 人。读者调整到达速度，看到达速度接近服务能力时队伍如何越排越长。过程用 `simulate` 推进，到达人数用固定种子的 `random` 生成，队伍中的人用 `c-list` 过渡进出。

```vue
<script setup>
import { ref, computed, watch } from 'vue'
import { random } from '@platform/random'
import { simulate } from '@platform/sim'

const rate = ref(2.6)
const SERVE = 3
let r = random(7)
const sim = simulate({
  init: () => {
    r = random(7)
    return { line: [], next: 0, history: [0] }
  },
  step: (s) => {
    for (let k = r.poisson(rate.value); k > 0; k--) s.line.push(s.next++)
    s.line.splice(0, SERVE)
    s.history.push(s.line.length)
    if (s.history.length > 61) s.history.shift()
    return s
  },
  speed: 4,
  start: 30,
})
watch(rate, () => sim.reset())
const path = computed(() =>
  sim.state.history.map((q, i) => `${i * 5},${78 - Math.min(q, 25) * 3}`).join(' ')
)
</script>

<template>
  <c-row>
    <c-slider label="到达速度" :min="1" :max="3.4" :step="0.2" unit=" 人/分" v-model="rate" />
    <button class="primary" @click="sim.running ? sim.pause() : sim.play()">{{ sim.running ? '暂停' : '播放' }}</button>
    <button @click="sim.reset()">从头开始</button>
  </c-row>
  <TransitionGroup name="c-list" tag="div" class="qu-line">
    <span v-for="id in sim.state.line.slice(0, 24)" :key="id" class="qu-person"></span>
  </TransitionGroup>
  <svg viewBox="0 0 300 80" width="100%">
    <polyline :points="path" fill="none" stroke="var(--chart-1)" stroke-width="2" />
  </svg>
  <p>第 {{ sim.t }} 分钟，队伍中有 {{ sim.state.line.length }} 人</p>
</template>

<style>
.qu-line { display: flex; flex-wrap: wrap; gap: 4px; min-height: 48px; margin-top: 12px; }
.qu-person { width: 16px; height: 20px; border-radius: var(--radius-sm); background: var(--chart-2); }
</style>
```

拖动数据点，观察最小二乘直线如何随之转动：点位于坐标平面中，读者直接拖动，直线和残差同步变化。

```vue
<script setup>
import { ref, computed } from 'vue'

const pts = ref([[1, 1.5], [2.5, 3.5], [4, 3], [5.5, 5]])
const fit = computed(() => {
  const n = pts.value.length
  const mx = pts.value.reduce((s, [x]) => s + x, 0) / n
  const my = pts.value.reduce((s, [, y]) => s + y, 0) / n
  const k =
    pts.value.reduce((s, [x, y]) => s + (x - mx) * (y - my), 0) / pts.value.reduce((s, [x]) => s + (x - mx) ** 2, 0)
  return { k, b: my - k * mx }
})
const line = (x) => fit.value.b + fit.value.k * x
</script>

<template>
  <c-plane :x="[0, 6]" :y="[0, 6]" :height="260">
    <c-line v-for="([x, y], i) in pts" :key="`r${i}`" :from="[x, y]" :to="[x, line(x)]" tone="muted" dashed />
    <c-curve :f="line" tone="2" :label="`y = ${fit.k.toFixed(2)}x + ${fit.b.toFixed(2)}`" />
    <c-point v-for="(p, i) in pts" :key="i" v-model="pts[i]" drag="xy" :snap="0.25" />
  </c-plane>
</template>
```

把四个状态码拖到所属的类别中：类别是格子，状态码是卡片，放置正确的显示为绿色。

```vue
<script setup>
import { ref } from 'vue'

const answer = { 200: '成功', 301: '重定向', 404: '客户端错误', 503: '服务端错误' }
const kinds = ['成功', '重定向', '客户端错误', '服务端错误']
const at = ref({ 待分类: ['404', '200', '503', '301'], 成功: [], 重定向: [], 客户端错误: [], 服务端错误: [] })
</script>

<template>
  <c-board v-model="at">
    <c-zone name="待分类" class="sc-tray">
      <c-piece v-for="c in at.待分类" :key="c" :name="c">{{ c }}</c-piece>
    </c-zone>
    <div class="sc-kinds">
      <c-zone v-for="k in kinds" :key="k" :name="k" :max="1">
        <small>{{ k }}</small>
        <c-piece v-for="c in at[k]" :key="c" :name="c" :class="{ 'sc-ok': answer[c] === k }">{{ c }}</c-piece>
      </c-zone>
    </div>
  </c-board>
</template>

<style>
.sc-tray { min-height: 52px; }
.sc-kinds { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; margin-top: 8px; }
.sc-kinds small { flex-basis: 100%; }
.sc-ok { border-color: var(--ok); background: var(--ok-wash); color: var(--ok-ink); }
</style>
```

