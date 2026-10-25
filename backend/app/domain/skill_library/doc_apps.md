---
name: doc-apps
title: 小程序的写法
description: 小程序的代码写法和设计规范。随 miniapp 技能下发，路径为 references/apps.md。
---

# 小程序的写法

什么时候用小程序、放进文档还是发到聊天，见 SKILL.md；这里讲决定用了之后怎么写。

目录：提交、代码是一个 Vue 组件、在文档中的位置、设计要求、设计规范、组件、过渡、三维图、模拟与随机数、在脚本中取颜色、例子。

## 提交

1. 用 `cheese_app(action=save)` 提交代码（`code`）和标题（`title`）。平台先在浏览器中以手机和桌面两种宽度、浅色和深色两种主题各运行一次，再像读者一样操作一遍：把滑块和数字输入框设到两端、选择每个选项、点击每个按钮、把可拖动的点拖到范围的角上。出现以下情况时不保存，并返回原因（操作后出现的，说明是哪一步操作）：运行报错，页面没有内容，高度持续增长，自查（`console.assert`）未通过，读者操作的控件没有绑定到变量，画面上出现 NaN。
2. 新建时返回一个块。把它放到文档中合适的位置，或者放进聊天消息（见 SKILL.md「放进文档还是发到聊天」），并把块的第二行改写为一句说明：小程序展示什么，最重要的结论是什么。在无法运行小程序的地方，读者只能看到这句说明；你以后读这份文档时，也只会读到这一句。
3. 修改已有的小程序：先用 `cheese_app(action=get)` 读取代码，改好后用 `cheese_app(action=save)` 提交，并带上 `app`（块中 `:::app` 后面的短号）。文档中块的版本号会同步更新。

保存成功时，结果中附有平台试运行时拍下的截图（桌面宽度和手机宽度）。看一眼画面是否讲清楚了、数字是否正确、手机上是否挤在一起；有问题就改好后带上 `app` 再保存一版。

## 代码是一个 Vue 组件

`code` 是一个 Vue 单文件组件：`<script setup>`、`<template>`，需要时加 `<style>`（不支持 `scoped`，类名要起得具体）。

- 只能从 `vue` 和平台提供的 `@platform/motion`、`@platform/sim`、`@platform/random`、`@platform/theme`（见下文「过渡」「模拟与随机数」「在脚本中取颜色」）导入，例如 `import { ref, computed, watch, onMounted, onUnmounted } from 'vue'`。没有其他库，不能联网，不能使用 `localStorage`、`alert` 和表单提交。
- 宽度与文档的栏宽一致：手机上三百多像素，桌面上六七百像素。按窄屏设计，用 flex 或 grid 排版，宽度不足时换行或缩小。
- 高度由内容决定。不要使用 `vh`、`height: 100%` 或固定的整屏高度：平台按内容高度调整外框，内容又随外框变高，高度会持续增长，保存会被拒绝。

## 在文档中的位置

小程序是文档中的一个块，位于正文之间。块的顶部由平台显示标题和说明，旁边有全屏按钮。发到聊天时是同一个块，下面几条同样适用。

- 小程序内不写标题，也不写解释性的段落：原理由正文讲解，小程序只放图形和供读者操作的控件。
- 高度控制在 400 至 550 像素，约半屏到一屏，读者看完后能很快回到正文。内容较多时，用分步播放或切换来收纳，不要纵向堆叠。
- 在无法运行的地方（读者无权查看所属文档、文档导出为文件），只显示块顶部的说明，因此这句说明要把结论写完整。

## 设计要求

编写代码前，先确定画面：画的是什么对象，读者操作时哪些部分随之变化。如果设想中的画面只有几个数字和一张表格，说明还没有找到要画的对象。

画面上的每个控件、每段运动，都要说得出它帮读者弄懂了什么：去掉它，读者会少懂什么。说不出来的，不要加。为了显得可以操作而加的滑块，为了显得生动而加的播放，只会让读者花时间找要紧的东西。

小程序要做到以下五点。讲清楚是目的；可交互、直观、运动是做到它的手段，取舍时按这个先后；正确是底线。

1. **讲清楚**：读者不读正文，只看画面、操作一两次，就能得出文档要讲的结论。
   - 默认值选最能说明问题的一组，因为多数读者不会改动它们。滑块的默认值不放在范围的两端，读者往两个方向都能拖。
   - 读者操作后，能看到支持结论的证据，例如越过临界点的那一刻、两个方案拉开差距的地方。
   - 决定结果的输入画在图上，例如每个对象何时出现、各需要多少资源。读者看不到输入，就看不出结果从何而来。
   - 读者要选的选项（规则、策略、算法），含义不是人人都知道时，在旁边用一句话写明它怎样运作。只有名称，读者选了也看不懂差别。
   - 用颜色、箭头、高亮或标注，指明读者该看哪里。没有提示，读者的注意力会落在无关的细节上。
   - 不误导：图形的长短和面积与数值成比例，坐标范围固定、不随数据缩放，否则画面上的差别不是真实的差别。
2. **可交互**：读者可以拖动、点击、输入、播放或作答，画面随之变化。
   - 每个控件对应读者会问的一个问题，例如「间隔再短一些会怎样」「样本再大一些呢」。只提供决定结论的那一两个量或结构，并限定范围。控件越多，读者越难找到要紧的那一个。
   - 操作方式与实际一致：本来就是拿起放下、排列顺序、两两配对的对象，让读者直接拖动；调整连续的数值用滑块；点击即可完成的操作不改成拖拽。
   - 操作后画面立即给出反馈。
   - 同一个量有几种表示（图形、数值、曲线）时，它们一起变化，读者才能把它们对应起来。
   - 提供一个回到初始状态的按钮，读者可以放心尝试。
3. **直观**：画出文档所讲的对象本身，数字只作辅助。
   - 完成后自查：遮住画面上的数字和文字，读者能否仍然看出发生了什么。如果不能，画面只是把数字换了一种排列；几张指标卡、一张通用图表加一张表格，读者从中得到的与阅读正文无异。
   - 最重要的量用位置或长度表示，次要的量才用颜色或面积：人眼比较位置和长度最准确，比较颜色深浅和面积大小最不准确。
   - 画成简化的示意图，只保留讲清这件事必需的部分。写实的细节只会分散注意力。
4. **运动**：所讲的东西本身就在运动时，让读者看着它运动：天体和物体的运动（行星绕转、三体、摆、抛体），物理现象（波的传播、扩散、碰撞），数学过程（迭代逼近、随机游走），算法和协议的执行（排序中元素的交换、数据包的收发、进程的调度）。各部分如何同时变化、如何相互影响，看着它运动比看一张事后画的图更容易理解。用 `simulate` 推进（见下文「模拟与随机数」）。
   - 结果用一张静态图就能看清的，不加播放。例如随时间变化的一条曲线：读者要比较的是不同条件下的曲线，用滑块调整条件、曲线立即重画即可；让曲线从左到右画出来，读者不会多懂什么。
   - 用播放让读者看着它运动。可以另加一个拖动时间的滑块，但不能只有它：拖滑块时，读者看到的是一张张静止的画面。
   - 连续变化的过程（物体的位置、电位、浓度、流动）用 `simulate` 以较小的 `dt` 推进，画面平滑地变化，不要一格一格地跳。由离散步骤组成的过程（博弈的轮次、算法的每一步）才逐步播放，用 `<c-player>`，步与步之间的变化用 `tween` 过渡。
   - 打开时停在起点，读者按播放后开始运动。
   - 节奏要看得清：速度慢到读者能分辨变化的先后。一闪而过的变化，读者看不清。
   - 读者能控制节奏：可以暂停、继续、逐步前进和重来。
   - 运动之后留下痕迹，例如轨迹或逐步的记录。动画转瞬即逝，留下的痕迹让读者可以回看、比较前后的状态。

   所讲的东西本身不在运动时，这一条不适用。
5. **正确**：
   - 数值按文档给出的数据计算，不另加文档没有给出的假设；计算结果保留合适的小数位数。
   - 文档中明确给出的数值（算例的结果、表格中的数据），在代码中用 `console.assert` 核对，第二个参数写明核对的是哪个数，例如 `console.assert(Math.abs(price(0) - 24) < 1e-9, '不征税时均衡价应为 24')`。核对未通过时，平台不保存，并返回未通过的那一条。
   - 参数取到范围两端时，画面仍然正确，不出现负的人数、越出画面的图形或非数字。
   - 练习的所有正确答案都判为正确；对每道题的每个正确答案都调用一次判题函数，确认判为正确。

各类小程序的要点：

- **改变条件**：结果画成便于比较的图形，并画出关键的界线（上限、临界点、目标），读者调整时能看到越过界线的那一刻。
- **比较方案**：几个方案并列展示，差别一目了然，选中的一项突出显示。
- **推演过程**：每一步发生了什么，以及它如何影响下一步，都画在图上。
- **动手组合**：读者每改动一处，画面立即显示新组合的结果；出现冲突或不满足条件的地方，在图上直接标出。
- **练习**：考的是文档所讲的内容：概念的运用、原理的推理、因果关系，而不是照着画面上的线索就能做出的操作。答案不能出现在画面上，也不显示能直接推出答案的数据。打开时给出题目和起点，不给出答案。把任务拆成几步，每一步给读者可以选择、拖放的部件，或者一个已经完成一部分的起点，不要只给一个空白的大输入框；需要读者自己输入时，只让他填其中一小段。每完成一步，画面就展示这一步的效果。所有正确答案都判为正确（相等的分数、写法不同的等价式子）；答错时指出错在哪一步、原因是什么，而不只是提示「错误」。

画面要简洁：

- 一个小程序只讲一件事，以一幅主图为中心。
- 数字标在它所描述的对象旁边。图上已有的数字，不在指标卡或表格中重复；指标卡只放图上放不下、又属于结论的一两个数。
- 结论只表述一次：一句话或一个状态标签，二者选一。同一个数在画面上也只出现一次。
- 除标签和单位外，画面上的文字不超过一两行。公式和计算过程，只在文档要求读者核对时给出。

绘制方法：

- 网格、列表、条形这类排列，用 HTML 和 CSS（flex、grid）实现，文字保持文档字号，在窄屏上依然清晰。读者操作后，其中的元素会因排版改变位置或大小的（换行、变宽、重新排列），在容器上加 `v-layout`，见下文「过渡」。
- 形状、连线和流向用内联 SVG，设置 `viewBox` 和 `width="100%"`，图形随栏宽缩放。SVG 中的文字也会随之缩小，因此少在 SVG 内放文字：标签和数字放在紧邻图形的 HTML 中（叠放在图上或排在旁边），字号就不会变小。
- 有坐标的图（函数、曲线、界线、面积、可拖动的点和向量）用 `<c-plane>`：其中的元素直接按坐标书写，坐标换算、坐标轴、拖动和过渡由它处理。横轴和纵轴默认各自缩放到填满画面；画几何图形（正方形、圆、角度、向量的变换）时加 `equal`，横竖用同一比例，正方形才是正方的，较宽裕的一边会向两侧扩展坐标范围。
- 对象本身是三维的，要讲的是起伏、形状或空间位置关系时，用 `<c-space>`（见下文「三维图」）：曲面的高低和鞍点、梯度的方向、切平面、立体的体积、空间中的轨迹、分子和晶体的结构。曲面的高低画成三维曲面，等高线作为补充，因为只看等高线的读者不容易看出哪里高、哪里低。数据只是恰好有三个变量、读者要读的是数值或比较时，用平面图，第三个变量用颜色或分组表示；不画三维柱状图或三维饼图。
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
| `<c-player>` 分步播放 | `:count`（总步数）`:labels`（每一步的名称，如 `["第 1 秒", …]`）`:interval`（自动播放时每步停留的毫秒数，默认 1000）`loop`；值是当前步的序号，**从 0 开始** | 逐步播放由离散步骤组成的过程（轮次、算法的每一步）：上一步、下一步、播放和暂停都由它处理，无需自己编写定时器。连续变化的过程用 `simulate` |
| `<c-stat>` 指标卡 | `label` `value` `delta`（下方的一行小字）`tone`（`good` 绿色、`bad` 红色） | 图上放不下、又属于结论的一两个数，几张放进一个 `<c-row>` |
| `<c-chip>` 状态标签，文字写在标签内 | `kind`：`ok`（✓）、`no`（✗）、`warn`（!） | 标明结论 |
| `<c-chart>` 图表 | `:option`（一份 ECharts 配置对象）`:height`（像素，200 至 320） | 较长的实测或给定数据序列的走势、分布 |
| `<c-math>` 公式 | `tex`（LaTeX）`display`（单独成行） | 展示计算公式 |
| `<c-row>` 横向排列 | `:gap`（像素）`align`（`start` `center` `end` `stretch`，默认 `end`） | 横向排列几项内容，宽度不足时自动换行 |
| `<c-space>` 三维空间 | 见下文「三维图」 | 三维的曲面、立体、向量和轨迹 |
| `<c-plane>` 坐标平面 | `:x` `:y`（坐标范围，`[最小值, 最大值]`）`:height`（像素，默认 240）`x-label` `y-label` `:grid`（网格线，默认显示）`equal`（横竖同一比例，见下） | 容纳下面四种元素并绘制坐标轴 |
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
- **排版变化**：从 `@platform/motion` 导入 `vLayout`，在容器上写 `v-layout`。容器的直接子元素因排版改变位置或大小时（例如网格中的卡片变宽后换到下一行），会从原来的位置和大小平滑过渡到新的。用 CSS 布局排出来的东西，位置和大小的变化无法用 `transition` 实现，要用它。

坐标平面和三维空间中的元素，拖放板中的块，以及指标卡中的数字，已经自带过渡，无需另行处理。读者在系统中设置了减少动态效果时，以上过渡都会关闭。

## 三维图

`<c-space>` 是一个三维坐标空间：其中的元素按坐标书写，透视、光照、遮挡、坐标轴、拖动和过渡由它处理。读者可以转动视角：电脑上按住拖动，手机上左右划动；全屏后可以向任意方向转动，并用双指缩放。

- z 轴朝上，右手系。角度一律用弧度。绕转轴转正角时，从转轴的正方向看是逆时针：绕 `[0, 0, 1]` 转正角，x 轴转向 y 轴。
- 坐标范围取得刚好容纳要画的对象：范围过大，对象缩在中间，读者看不清。
- 三个坐标轴默认各自缩放，画成一个正方体，适合 z 的量级与 x、y 不同的函数图像。画实体（球、立方体、分子）或要比较长度和角度时加 `equal`，三轴用同一比例，否则球会画成椭球。
- `:view` 是打开时的视角，`[水平角, 仰角]`：水平角从 x 轴正方向起算，仰角是视线高出 xy 平面的角度。默认的 `[-1, 0.45]` 从右前方稍高处看。选一个能同时看清要点的角度；改变 `:view` 时，镜头转到新的视角。
- 被挡住的点、线和箭头以淡色透出。超出坐标范围的曲面、曲线、线段和多边形会被裁去。
- 函数中用到的变量改变时，曲面、曲线和向量场随之重画。
- 球、立方体这类实体只用于对象本身就是这个形状的场合（原子、物体）；示意关系时，点、线和箭头更清楚。

| 标签（都放在 `c-space` 内） | 属性 | 用途 |
| --- | --- | --- |
| `<c-space>` 三维空间 | `:x` `:y` `:z`（坐标范围）`:height`（像素，默认 320）`:view` `equal` `axes="none"`（不画坐标轴和底面网格）`x-label` `y-label` `z-label` | 容纳下面的元素 |
| `<c-point>` 点 | `v-model`（`[x, y, z]`）`drag`（读者可拖动的方向：`x` `y` `z` `xy` `xz` `yz`，最多两个）`:snap` `:on`（函数 (x, y) → z，点只能在这个曲面上移动）`:from`（`[x, y, z]`，从此处向点画一个箭头，即向量）`drop`（从点向底面画一条垂线，标出它在 xy 平面上的位置）`tone` `label` | 可拖动的点、向量、标出的位置 |
| `<c-line>` 线段 | `:from` `:to` `tone` `dashed` `label` | 连线、轴线、棱 |
| `<c-curve>` 曲线 | `:f`（参数方程 t → `[x, y, z]`）和 `:from` `:to`（t 的范围，默认 0 到 1），或 `:points`（依次经过的点）；`tone` `dashed` `label` | 空间曲线、运动轨迹 |
| `<c-area>` 平面多边形 | `:points`（顶点，同在一个平面上）`tone` `:opacity`（默认 0.35）`label` | 截面、平行四边形、区域 |
| `<c-surface>` 曲面 | `:f` 写成以下三种之一：`(x, y) => z`；`(u, v) => [x, y, z]`，加 `:u` `:v`（参数范围）；`(x, y, z) => 值`，画出值为 0 的曲面（平面、球面、二次曲面、等势面），坐标范围要取得足以容纳整个曲面，否则超出的部分被裁去，闭合的曲面会变成开口的。`:x` `:y` `:z`（范围，默认整个空间）`:color`（函数 (x, y, z) → 0 到 1，按色带着色）`contours`（等高线，画在曲面上和底面上，只用于 `(x, y) => z`）`:opacity` `wire`（只画网格线）`tone` `label` | 函数图像、参数曲面、平面 |
| `<c-sphere>` 球 | `:center` `:radius` `tone` `:opacity` `wire` `label` | 原子、天体 |
| `<c-box>` 长方体 | `:from` `:to`（相对的两个角）`tone` `:opacity` `wire` `label` | 物块、体积单元 |
| `<c-cylinder>` 圆柱，`<c-cone>` 圆锥 | `:from` `:to`（两端截面的中心；圆锥的尖在 `to`）`:radius` `tone` `:opacity` `wire` `label` | 杆、化学键、圆盘 |
| `<c-mesh>` 多面体 | `:vertices`（顶点）`:faces`（每个面的顶点序号，从 0 开始）`tone` `:opacity` `wire` `label` | 任意多面体：变换后的立方体、晶体外形 |
| `<c-points>` 点云 | `:points` `:size`（像素，默认 3）`:color` `tone` `:opacity` | 大量样本、粒子 |
| `<c-field>` 向量场 | `:f`（函数 (x, y, z) → `[vx, vy, vz]`）`:n`（每个方向的箭头数，默认 6，也可以写 `[nx, ny, nz]`）`:x` `:y` `:z` `tone` | 电场、流速场 |
| `<c-text>` 文字 | `:at`（`[x, y, z]`）；标签内写文字或 `<c-math>` | 不属于某个点的说明 |
| `<c-group>` 组 | `:position`（平移）`:rotate`（`[转轴, 角度]`，例如 `[[0, 0, 1], 0.5]`）`:scale`；组内的元素按组的坐标书写，可以嵌套 | 一起移动或转动的部件：机械臂的各节，自转加公转 |

## 模拟与随机数

所讲的东西本身在运动时（轨道、摆、传播、算法的执行），用 `@platform/sim` 的 `simulate` 推进，不要自己写定时器：

```js
const sim = simulate({
  init: () => ({ s: 990, i: 10, r: 0 }), // 初始状态
  step: (state, dt, t) => next,          // 由当前状态算出下一步的状态，可以直接修改 state 后返回它
  dt: 1,                                 // 每一步代表的时长，默认 1
  speed: 10,                             // 播放时每秒走几步，默认 10
  end: 60,                               // 过程在哪个时刻结束（与 t 同一单位）；过程不会结束时省略
})
```

返回的 `sim` 可以直接在模板中使用：`sim.state` 是当前状态，`sim.t` 是当前时刻，`sim.running` 表示是否在播放，`sim.done` 表示是否已到终点；`sim.play()`、`sim.pause()`、`sim.reset()` 分别是播放、暂停和回到开头，`sim.advance(n)` 一次前进 `n` 步。打开时停在开头。播放到 `end` 时刻自动停下，再按播放从头开始。播放和暂停用普通的 `<button>`。读者改了参数要从头开始时，写 `watch(参数, () => sim.reset())`。需要按微分方程精确推进时（轨道、摆），用同一模块的 `rk4(f, y, t, dt)`：`f(t, y)` 返回 `y` 各分量的变化率，`rk4` 返回前进一步之后的 `y`。

需要随机数时，用 `@platform/random` 的 `random(种子)`，不用 `Math.random`。同一个种子每次产生同样的随机数，平台检查时拍下的画面就是读者看到的画面，`console.assert` 也能核对模拟的结果。它提供 `next()`（0 到 1 之间的数）、`uniform(a, b)`、`int(a, b)`、`normal(均值, 标准差)`、`exponential(速率)`、`poisson(均值)`、`pick(列表)`、`shuffle(列表)`。要让 `reset()` 后重现同样的过程，在 `init` 中重新创建它。

## 在脚本中取颜色

在 `<canvas>` 上作画时无法写 `var(--chart-1)`，用 `@platform/theme` 的 `color('chart-1')` 取得颜色；`color('1')` 到 `color('8')`、`color('ok')`、`color('muted')` 这类简写与 `tone` 一致。把作画的代码放进 `watchEffect`，读者切换深浅色时会自动重画。

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

行星绕恒星运行：读者调整行星的初速度，看轨道从圆变成越来越扁的椭圆。行星本身在运动，用 `simulate` 推进，每一步用 `rk4` 按万有引力求解，走过的路径留下轨迹。

```vue
<script setup>
import { ref, computed, watch } from 'vue'
import { simulate, rk4 } from '@platform/sim'

// 取 GM = 1：行星从 (1, 0) 出发，初速度垂直向上；速度为 1 时是圆轨道
const speed = ref(1.1)
const gravity = (t, [x, y, vx, vy]) => {
  const r3 = Math.hypot(x, y) ** 3
  return [vx, vy, -x / r3, -y / r3]
}
const sim = simulate({
  init: () => ({ body: [1, 0, 0, speed.value], trail: [[1, 0]] }),
  step: (s, dt, t) => {
    s.body = rk4(gravity, s.body, t, dt)
    s.trail.push([s.body[0], s.body[1]])
    return s
  },
  dt: 0.02,
  speed: 60,
  end: 24, // 速度取到 1.25 时也能绕完一圈
})
watch(speed, () => sim.reset())

const X = (x) => 190 + x * 32 // 1 个单位 = 32 像素，恒星在 (190, 70)
const Y = (y) => 70 - y * 32
const path = computed(() => sim.state.trail.map(([x, y]) => `${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join(' '))

let check = [1, 0, 0, 1]
for (let i = 0; i < 300; i++) check = rk4(gravity, check, i * 0.02, 0.02)
console.assert(Math.abs(Math.hypot(check[0], check[1]) - 1) < 1e-3, '速度为 1 时行星应留在半径 1 的圆上')
</script>

<template>
  <c-row>
    <c-slider label="初速度" :min="0.8" :max="1.25" :step="0.05" v-model="speed" />
    <button class="primary" @click="sim.running ? sim.pause() : sim.play()">{{ sim.running ? '暂停' : '播放' }}</button>
    <button @click="sim.reset()">回到开头</button>
  </c-row>
  <svg viewBox="0 0 300 140" width="100%">
    <polyline :points="path" fill="none" stroke="var(--chart-1)" stroke-width="1.5" />
    <circle cx="190" cy="70" r="6" fill="var(--chart-2)" />
    <circle :cx="X(sim.state.body[0])" :cy="Y(sim.state.body[1])" r="4" fill="var(--ink)" />
  </svg>
</template>
```

拖动数据点，观察最小二乘直线如何随之转动：点位于坐标平面中，读者直接拖动，直线和残差同步变化。

```vue
<script setup>
import { ref, computed } from 'vue'

const pts = ref([[1, 1.5], [2.5, 3.5], [4, 3], [5.5, 5]])
// 所有点的横坐标相同时，直线是竖直的，没有斜率，这时不画直线
const fit = computed(() => {
  const n = pts.value.length
  const mx = pts.value.reduce((s, [x]) => s + x, 0) / n
  const my = pts.value.reduce((s, [, y]) => s + y, 0) / n
  const sxx = pts.value.reduce((s, [x]) => s + (x - mx) ** 2, 0)
  if (sxx === 0) return null
  const k = pts.value.reduce((s, [x, y]) => s + (x - mx) * (y - my), 0) / sxx
  return { k, b: my - k * mx }
})
const line = (x) => fit.value.b + fit.value.k * x
</script>

<template>
  <c-plane :x="[0, 6]" :y="[0, 6]" :height="260">
    <template v-if="fit">
      <c-line v-for="([x, y], i) in pts" :key="`r${i}`" :from="[x, y]" :to="[x, line(x)]" tone="muted" dashed />
      <c-curve :f="line" tone="2" :label="`y = ${fit.k.toFixed(2)}x + ${fit.b.toFixed(2)}`" />
    </template>
    <c-point v-for="(p, i) in pts" :key="i" v-model="pts[i]" drag="xy" :snap="0.25" />
  </c-plane>
</template>
```

两个向量的叉积：拖动 xy 平面内的 a 和 b，a × b 始终垂直于二者，长度等于以 a、b 为边的平行四边形的面积。读者转动视角，能看清它与平面垂直。

```vue
<script setup>
import { ref, computed } from 'vue'

// a、b 在 xy 平面内拖动；a × b 垂直于二者，长度等于以 a、b 为边的平行四边形的面积
const a = ref([1.5, 0.5, 0])
const b = ref([0.5, 1.5, 0])
const cross = computed(() => {
  const [a1, a2, a3] = a.value
  const [b1, b2, b3] = b.value
  return [a2 * b3 - a3 * b2, a3 * b1 - a1 * b3, a1 * b2 - a2 * b1]
})
const area = computed(() => Math.hypot(...cross.value))
const corner = computed(() => a.value.map((v, i) => v + b.value[i]))
console.assert(Math.abs(area.value - 2) < 1e-9, '初始平行四边形的面积应为 2')
</script>

<template>
  <c-space :x="[-2, 2]" :y="[-2, 2]" :z="[-4, 4]" equal :view="[-0.9, 0.3]" :height="360">
    <c-area :points="[[0, 0, 0], a, corner, b]" tone="1" />
    <c-point v-model="a" :from="[0, 0, 0]" drag="xy" :snap="0.25" tone="2" label="a" />
    <c-point v-model="b" :from="[0, 0, 0]" drag="xy" :snap="0.25" tone="3" label="b" />
    <c-point :value="cross" :from="[0, 0, 0]" tone="warn" :label="`a × b，长度 ${area.toFixed(2)}`" />
  </c-space>
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

