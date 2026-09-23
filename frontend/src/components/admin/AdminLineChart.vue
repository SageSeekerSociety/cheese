<script lang="ts">
/** 一条折线。`style` 只决定线型；颜色由组件按线型发（见 SERIES_COLOR / DASH）。
 *
 *  三种而不是两种：反馈那一类的生命周期是**三档**（新增 → 解决 → 上线），而后两档是
 *  两件事 —— 一条反馈「修好了」和「上线了」对提交者是两回事（见
 *  `domain/feedback/repositories.py` 的 `CLOSED_STATUSES`）。只画前两档的话，图上
 *  那条最该被看见的尾巴整个不存在。 */
export interface ChartSeries {
  name: string
  values: number[]
  style: 'solid' | 'dashed' | 'dotted'
}
</script>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminNoteTip from '@/components/admin/AdminNoteTip.vue'
import { fmtNum } from '@/lib/usageFormat'

// 看板上的折线图（配色见 §7.5）。
//
// **手写 SVG，不引图表库。** 这一张图只有两三条线，库要带来的是一份自己的主题、
// 一套尺寸协议、一个图例 DOM，以及一份要跟着这份设计系统再对齐一遍的配色 —— 而
// 项目里其实没有任何图表依赖（package.json 里没有 chart/d3/echarts 这一类），为两张
// 图装一个是把整条依赖链引进来换几十行路径。
//
// **几条线只用中性阶的深浅 + 线型区分，不用几个色相。** 琥珀在这个产品里只能当填充
// （§7.3：浅色下 2.65:1，当线色读不出来），而随便挑几个色相必然有一个落在低对比度
// 上；线型这一重信号在灰度截图里也活着（§14 第 7 条就是这么量的）。
//
// **真实像素渲染，不再 viewBox 等比缩放。** 旧版按设计尺寸 628px 算坐标、用 viewBox
// 缩放到容器宽 —— 宽档容器（~840px）会把 12px 轴字等比放大到 ~16px，有效字号脱离
// 档位。现在根 div 挂 ResizeObserver，画布宽跟着容器走（280–1400 收口），SVG 永远
// 1:1，字号线宽永远是设计值。
//
// 图形本身 `aria-hidden`：图里没有读得出来的东西。数据在下面那张表里 —— §7.5 要求的
// 表格孪生体，默认折叠，`<details>` 原生的键盘行为（Tab 到、回车展开）就够了；hover
// tooltip 只是指针用户的快捷方式，不替代它。点某一天 `emit('select', i)`，由页面把它
// 翻成队列的筛选参数。

const props = withDefaults(
  defineProps<{
    title: string
    /** 与每条 series 的 `values` 等长。 */
    xLabels: string[]
    series: ChartSeries[]
    loading?: boolean
    /** 口径注。给了就在标题旁画 info tip（散行注脚的归宿，见 §8 收编）。 */
    note?: string
  }>(),
  { loading: false }
)

const emit = defineEmits<{ (e: 'select', index: number): void }>()

const { t } = useI18n()

/** 画布高恒定 180；宽跟着容器走（下方 ResizeObserver），上下界是「还能读」的两端：
 *  窄于 280 轴字会互相压，宽于 1400 折线被拉成近乎直线的平坡。 */
const VIEW_H = 180
const MIN_W = 280
const MAX_W = 1400
/** 首次渲染的设计宽（RO 回调前的一帧；也是 happy-dom 这类没有 RO 的环境里的工作值）。 */
const FALLBACK_W = 628
const PAD_RIGHT = 8
const PAD_TOP = 8
const PAD_BOTTOM = 20
const PLOT_H = VIEW_H - PAD_TOP - PAD_BOTTOM

const canvasW = ref(FALLBACK_W)
const root = ref<HTMLElement | null>(null)

let observer: ResizeObserver | null = null

onMounted(() => {
  // happy-dom 与老浏览器没有 ResizeObserver：守住 `undefined`，画布停在 FALLBACK_W
  // （设计宽），图照样读得完 —— 只是轴字不再随容器微调。
  if (!root.value || typeof ResizeObserver === 'undefined') return
  observer = new ResizeObserver((entries) => {
    const w = entries[0]?.contentRect.width
    if (w) canvasW.value = Math.min(MAX_W, Math.max(MIN_W, Math.round(w)))
  })
  observer.observe(root.value)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
})

/** 线型是第一重信号（灰度下也在），颜色是第二重。三条线各占一档中性色，深浅跟着
 *  「走到哪一步」走：新增最深、解决次之、上线最浅。 */
const SERIES_COLOR: Record<ChartSeries['style'], string> = {
  solid: 'var(--text)',
  dashed: 'var(--muted)',
  dotted: 'var(--faint)',
}

/** 线型 → `stroke-dasharray`。**一处定义、两处用**（图例里那一小段线样和图上那条线）：
 *  两处各写一遍的话，以后改线型的人会在图例上看到一种画法、在图上是另一种。 */
const DASH: Record<ChartSeries['style'], string | undefined> = {
  solid: undefined,
  dashed: '4 3',
  dotted: '1 3',
}

/** 点标记画成空心还是实心。只有实线是实心的；两种虚线都画空心 —— 空心圆用卡片底色填
 *  （不是 `fill: none`，后者会让线从洞里穿过去，看起来仍是实心）。 */
const hollow = (s: ChartSeries): boolean => s.style !== 'solid'

/** 空态只认「结构上没有东西」：没有一天的标签，或者一条线都没有。**全 0 不算空** ——
 *  那是「这一周真的没有新增」，是一条压在底上的线；把它换成「暂无数据」等于对读的人
 *  说平台还没开始用。 */
const empty = computed(() => props.xLabels.length === 0 || props.series.length === 0)

/** 纵轴上界。全是 0 时取 1，否则所有点都落在 y=0、看起来像没画出东西。 */
const maxValue = computed(() => Math.max(1, ...props.series.flatMap((s) => s.values)))

/** y 轴四条刻度：上界、2/3、1/3、0。**相邻等值去重** —— max ≤ 2 时中间档会和两端
 *  撞成同一个数（max=1 的刻度表是 1/1/0/0），重画一遍只是在同一个位置叠两遍字。
 *  文案用 `fmtNum` 全值（不用 `fmtSI`：缩写给读屏和 number-display 契约制造麻烦，
 *  四个刻度用全值摆得下，PAD_LEFT 已经动态）。 */
const yTicks = computed(() => {
  const max = maxValue.value
  const raw = [max, Math.round((2 * max) / 3), Math.round(max / 3), 0]
  return raw.filter((v, i) => i === 0 || v !== raw[i - 1])
})

/** y 轴刻度文案最长者的字符数 —— 90 天窗口的七位数刻度不该被裁掉。 */
const maxLabelLen = computed(() => Math.max(...yTicks.value.map((v) => fmtNum(v).length)))

/** 左 padding 跟着刻度文案走：每字符约 7px（12px 轴字的数字宽），两端各留一口气。 */
const padLeft = computed(() => 8 + 7 * maxLabelLen.value)

const plotW = computed(() => canvasW.value - padLeft.value - PAD_RIGHT)

const step = computed(() => (props.xLabels.length > 1 ? plotW.value / (props.xLabels.length - 1) : 0))

const x = (i: number): number => padLeft.value + i * step.value
const y = (v: number): number => PAD_TOP + PLOT_H - (v / maxValue.value) * PLOT_H

const path = (s: ChartSeries): string => s.values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i)} ${y(v)}`).join('')

/** x 轴抽稀：标 `ceil(n/6)` 的倍数位与最后一天。7 天全标（step=1），30 天标 6+1 个，
 *  90 天标 7 个 —— 轴上永远读得出两端和走向，中间的交给 hover。 */
const xTicks = computed(() => {
  const n = props.xLabels.length
  if (n === 0) return []
  const stride = Math.ceil(n / 6)
  const out: { i: number; anchor: 'start' | 'middle' | 'end' }[] = []
  for (let i = 0; i < n; i += stride) {
    out.push({ i, anchor: i === 0 ? 'start' : 'middle' })
  }
  if (n - 1 > out[out.length - 1]!.i) out.push({ i: n - 1, anchor: 'end' })
  else out[out.length - 1]!.anchor = 'end'
  return out
})

/** 点标记只在点数 ≤ 45 时画：90 个点在 1400px 宽下间距 ~15px，实心圆会糊成一条粗线
 *  —— 那时线本身就是形状，点只剩噪音。tooltip 不受点数影响。 */
const POINTS_LIMIT = 45

const hover = ref<number | null>(null)

/** 指针落在哪一天。用 `getBoundingClientRect` 换算而不是 `offsetX`：SVG 可能被 CSS
 *  缩放过，`offsetX` 给的是屏幕坐标，直接当画布坐标用会偏。 */
function indexAt(event: MouseEvent): number {
  const rect = (event.currentTarget as SVGRectElement).ownerSVGElement?.getBoundingClientRect()
  if (!rect || rect.width === 0 || props.xLabels.length === 0) return 0
  const canvasX = ((event.clientX - rect.left) / rect.width) * canvasW.value
  const raw = step.value > 0 ? Math.round((canvasX - padLeft.value) / step.value) : 0
  return Math.min(props.xLabels.length - 1, Math.max(0, raw))
}

const onMove = (event: MouseEvent): void => {
  hover.value = indexAt(event)
}
const onLeave = (): void => {
  hover.value = null
}
const onClick = (event: MouseEvent): void => {
  emit('select', indexAt(event))
}

/** tooltip 的横坐标：跟着数据点走是它的职责（「hover 不改位置」管的是被 hover 的
 *  元素自己不动），两端各留 8% 防止浮层被卡片边缘裁掉。 */
const tooltipLeft = computed(() => {
  if (hover.value === null) return 0
  const px = x(hover.value)
  return Math.min(canvasW.value * 0.92, Math.max(canvasW.value * 0.08, px))
})
</script>

<template>
  <div ref="root" class="alc">
    <div class="alc__head">
      <span class="alc__title t-eyebrow-read">{{ title }}</span>
      <AdminNoteTip v-if="note" :text="note" />
      <span class="alc__legend">
        <span v-for="s in series" :key="s.name" class="alc__legend-item">
          <!-- 图例里的线样和图上那条线是同一套参数（颜色 + `DASH` 那张表），换了线型
               的人不会在两处看到两种画法。 -->
          <svg class="alc__swatch" width="16" height="4" viewBox="0 0 16 4" aria-hidden="true">
            <line
              x1="0"
              y1="2"
              x2="16"
              y2="2"
              stroke-width="2"
              :style="{ stroke: SERIES_COLOR[s.style] }"
              :stroke-dasharray="DASH[s.style]"
            />
          </svg>
          <span class="alc__ink">{{ s.name }}</span>
        </span>
      </span>
    </div>

    <div v-if="loading" class="alc__skeleton">
      <v-skeleton-loader type="text" class="alc__skel alc__skel--title" />
      <v-skeleton-loader type="image" class="alc__skel alc__skel--plot" />
    </div>

    <p v-else-if="empty" class="alc__none">
      <span class="alc__none-title">{{ t('feedback.dashboard.empty.title') }}</span>
      <span class="alc__none-desc">{{ t('feedback.dashboard.empty.desc') }}</span>
    </p>

    <template v-else>
      <div class="alc__stage">
        <svg
          class="alc__plot"
          :width="canvasW"
          :height="VIEW_H"
          :viewBox="`0 0 ${canvasW} ${VIEW_H}`"
          aria-hidden="true"
        >
          <line
            v-for="(tick, i) in yTicks"
            :key="`grid-${i}`"
            class="alc__grid"
            :x1="padLeft"
            :x2="canvasW - PAD_RIGHT"
            :y1="y(tick)"
            :y2="y(tick)"
          />
          <line
            class="alc__baseline"
            :x1="padLeft"
            :x2="canvasW - PAD_RIGHT"
            :y1="PAD_TOP + PLOT_H"
            :y2="PAD_TOP + PLOT_H"
          />
          <path
            v-for="s in series"
            :key="s.name"
            class="alc__line"
            :d="path(s)"
            :style="{ stroke: SERIES_COLOR[s.style] }"
            :stroke-dasharray="DASH[s.style]"
          />
          <!-- 标记：实线系列是实心圆，虚线系列是空心圆（§7.5，判据见 `hollow`）。空心那
               一种用卡片的底色填，而不是 `fill: none` —— 后者会让线从洞里穿过去，看起来
               还是实心的。点数 > 45 不画（见 `POINTS_LIMIT`）。 -->
          <template v-for="s in series" :key="`pt-${s.name}`">
            <template v-if="s.values.length <= POINTS_LIMIT">
              <circle
                v-for="(v, i) in s.values"
                :key="i"
                class="alc__point"
                :cx="x(i)"
                :cy="y(v)"
                r="2"
                :stroke-width="hollow(s) ? 2 : 0"
                :style="{
                  fill: hollow(s) ? 'var(--surface)' : SERIES_COLOR[s.style],
                  stroke: SERIES_COLOR[s.style],
                }"
              />
            </template>
          </template>
          <line
            v-if="hover !== null"
            class="alc__hover"
            :x1="x(hover)"
            :x2="x(hover)"
            :y1="PAD_TOP"
            :y2="PAD_TOP + PLOT_H"
          />
          <text
            v-for="tick in xTicks"
            :key="`x-${tick.i}`"
            class="alc__ink"
            :x="x(tick.i)"
            :y="VIEW_H - 4"
            :text-anchor="tick.anchor"
          >
            {{ xLabels[tick.i] }}
          </text>
          <text
            v-for="(tick, i) in yTicks"
            :key="`y-${i}`"
            class="alc__ink"
            :x="padLeft - 4"
            :y="y(tick) + 4"
            text-anchor="end"
          >
            {{ fmtNum(tick) }}
          </text>
          <!-- 整块绘图区是一张点击面，放在最后（在最上层才收得到指针）。它同时管 hover
               竖线：竖线本身是 1px、收不到指针，只能由这一层算。 -->
          <rect
            class="alc__hit"
            x="0"
            y="0"
            :width="canvasW"
            :height="VIEW_H"
            fill="none"
            pointer-events="all"
            @mousemove="onMove"
            @mouseleave="onLeave"
            @click="onClick"
          />
        </svg>

        <!-- hover 读数：日期 + 各系列当天的值。`pointer-events: none` —— 它跟着指针
             的数据走，自己绝不收指针（否则会挡住 hit 层、闪烁）。出现/消失不做过渡。 -->
        <div v-if="hover !== null" class="alc__tooltip" :style="{ left: `${tooltipLeft}px` }">
          <span class="alc__tip-date t-eyebrow-read">{{ xLabels[hover] }}</span>
          <span v-for="s in series" :key="s.name" class="alc__tip-row">
            <svg class="alc__swatch" width="16" height="4" viewBox="0 0 16 4" aria-hidden="true">
              <line
                x1="0"
                y1="2"
                x2="16"
                y2="2"
                stroke-width="2"
                :style="{ stroke: SERIES_COLOR[s.style] }"
                :stroke-dasharray="DASH[s.style]"
              />
            </svg>
            <span class="alc__tip-name">{{ s.name }}</span>
            <span class="alc__tip-val t-dense t-num">{{ fmtNum(s.values[hover] ?? 0) }}</span>
          </span>
        </div>
      </div>

      <details class="alc__data">
        <summary class="alc__toggle">{{ t('feedback.chart.dataTable') }}</summary>
        <table class="alc__table">
          <thead>
            <tr>
              <th scope="col">{{ t('feedback.chart.date') }}</th>
              <th v-for="s in series" :key="s.name" scope="col">{{ s.name }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(label, i) in xLabels" :key="i">
              <th scope="row" class="alc__rowhead">{{ label }}</th>
              <td v-for="s in series" :key="s.name" class="alc__cell t-num">{{ s.values[i] ?? 0 }}</td>
            </tr>
          </tbody>
        </table>
      </details>
    </template>
  </div>
</template>

<style scoped>
.alc {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
}

.alc__head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.alc__legend {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-left: auto;
}

.alc__legend-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

/* 轴标签与图例共用一个类：两处的要求是同一句（§7.5：`--muted` 12px）。`fill` 取
   `currentColor` 是为了让 `color` 成为唯一那个色 —— SVG 文字不认 `color`，只认
   `fill`，两处各写一遍迟早会分叉。 */
.alc__ink {
  font-size: 12px;
  color: var(--muted);
  fill: currentColor;
}

.alc__swatch {
  flex: 0 0 auto;
}

/* 画布与 tooltip 的舞台：tooltip 按像素绝对定位在这里，横坐标与 SVG 画布同一把尺。 */
.alc__stage {
  position: relative;
}

.alc__plot {
  display: block;
  max-width: 100%;
  height: auto;
}

.alc__tooltip {
  position: absolute;
  top: 8px;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 12px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-top-left-radius: var(--radius-md);
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  border-bottom-left-radius: var(--radius-md);
  /* 浮层允许投影（design-system §3.4：菜单/弹窗/抽屉那一类）。 */
  box-shadow: var(--shadow-1);
  pointer-events: none;
  transform: translateX(-50%);
  white-space: nowrap;
}

.alc__tip-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.alc__tip-name {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.alc__tip-val {
  margin-left: auto;
  color: var(--ink);
}

.alc__grid {
  stroke: var(--line);
  stroke-width: 1;
}

.alc__baseline {
  stroke: var(--line);
  stroke-width: 1;
}

.alc__line {
  fill: none;
  stroke-width: 2;
}

/* `.alc__point` 不在这里写 `stroke-width`：实心/空心就差这一个值，写在 CSS 里会把
   模板上按线型绑的那一个盖掉（CSS 胜过表现属性），空心圆会变成一圈看不见的 0。 */

.alc__hover {
  stroke: var(--line-2);
  stroke-width: 1;
}

.alc__hit {
  cursor: pointer;
}

/* 表格孪生体。默认折叠（`<details>` 不带 `open`），字号比正文小一档：它是给读屏和
   「想抄一个数」的人用的，不是这一页的主角。 */
.alc__toggle {
  color: var(--muted);
  font-size: 12.5px;
  line-height: var(--lh-12);
  cursor: pointer;
}

.alc__table {
  width: 100%;
  margin-top: 8px;
  border-collapse: collapse;
  font-size: 12.5px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.alc__table th,
.alc__table td {
  padding: 4px 8px;
  text-align: right;
  border-bottom: 1px solid var(--line);
}

.alc__table th:first-child,
.alc__table td:first-child {
  text-align: left;
}

.alc__table tr:last-child th,
.alc__table tr:last-child td {
  border-bottom: 0;
}

.alc__rowhead {
  font-weight: 400;
}

.alc__none {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
}

.alc__none-title {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
}

/* 空态的副文案照 §9.2 那一套（主 15/600/--ink，副 13/--muted，中间 8px）。 */
.alc__none-desc {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.alc__skel--title {
  width: 96px;
}

.alc__skel--plot {
  width: 100%;
}

.alc__skel :deep(.v-skeleton-loader__text) {
  height: 12px;
  margin: 0;
  background: var(--fill-2);
}

.alc__skel--plot :deep(.v-skeleton-loader__image) {
  height: 180px;
  margin: 0;
  background: var(--fill-2);
}
</style>
