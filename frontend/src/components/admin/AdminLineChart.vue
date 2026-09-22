<script lang="ts">
/** 一条折线。`style` 只决定线型；颜色由组件按线型发（见 SERIES_COLOR）。 */
export interface ChartSeries {
  name: string
  values: number[]
  style: 'solid' | 'dashed'
}
</script>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

// 看板上的折线图（§4.2 的左卡，配色见 §7.5）。
//
// **手写 SVG，不引图表库。** 这一张图只有两条线、七个点，库要带来的是一份自己的主题、
// 一套尺寸协议、一个图例 DOM，以及一份要跟着这份设计系统再对齐一遍的配色 —— 而
// 项目里其实没有任何图表依赖（package.json 里没有 chart/d3/echarts 这一类），为两张
// 图装一个是把整条依赖链引进来换几十行路径。
//
// **两条线只用中性阶的深浅 + 线型区分，不用两个色相。** 琥珀在这个产品里只能当填充
// （§7.3：浅色下 2.65:1，当线色读不出来），而随便挑两个色相必然有一个落在低对比度
// 上；实线/虚线这一重信号在灰度截图里也活着（§14 第 7 条就是这么量的）。
//
// 坐标按设计尺寸（628×180）算，再用 `viewBox` 缩放到容器宽：容器按 §4.2 就是 660px
// （卡内 628），缩放比因此是 1 —— 字号与线宽都是设计值。换别的宽度时整张图等比缩放，
// 不会出现「线宽 2px、点被拉扁」。
//
// 图形本身 `aria-hidden`：图里没有读得出来的东西。数据在下面那张表里 —— §7.5 要求的
// 表格孪生体，默认折叠，`<details>` 原生的键盘行为（Tab 到、回车展开）就够了。
// 点某一天 `emit('select', i)`，由页面把它翻成队列的筛选参数。

const props = withDefaults(
  defineProps<{
    title: string
    /** 与每条 series 的 `values` 等长。 */
    xLabels: string[]
    series: ChartSeries[]
    loading?: boolean
  }>(),
  { loading: false }
)

const emit = defineEmits<{ (e: 'select', index: number): void }>()

const { t } = useI18n()

/** 画布的设计尺寸。轴标签要占位置，所以绘图区比画布小一圈。 */
const VIEW_W = 628
const VIEW_H = 180
const PAD_LEFT = 32
const PAD_RIGHT = 8
const PAD_TOP = 8
const PAD_BOTTOM = 20
const PLOT_W = VIEW_W - PAD_LEFT - PAD_RIGHT
const PLOT_H = VIEW_H - PAD_TOP - PAD_BOTTOM

/** 线型是第一重信号（灰度下也在），颜色是第二重。两条线各占一档中性色。 */
const SERIES_COLOR: Record<ChartSeries['style'], string> = {
  solid: 'var(--text)',
  dashed: 'var(--muted)',
}

/** 空态只认「结构上没有东西」：没有一天的标签，或者一条线都没有。**全 0 不算空** ——
 *  那是「这一周真的没有新增」，是一条压在底上的线；把它换成「暂无数据」等于对读的人
 *  说平台还没开始用。 */
const empty = computed(() => props.xLabels.length === 0 || props.series.length === 0)

/** 纵轴上界。全是 0 时取 1，否则所有点都落在 y=0、看起来像没画出东西。 */
const maxValue = computed(() => Math.max(1, ...props.series.flatMap((s) => s.values)))

const step = computed(() => (props.xLabels.length > 1 ? PLOT_W / (props.xLabels.length - 1) : 0))

const x = (i: number): number => PAD_LEFT + i * step.value
const y = (v: number): number => PAD_TOP + PLOT_H - (v / maxValue.value) * PLOT_H

/** 四条横线，含 0 与上界。等分而不是「按整数刻度」：这一页的绝对值不重要（点进去才是
 *  数据），重要的是形状。 */
const gridLines = computed(() => [0, 1, 2, 3].map((i) => PAD_TOP + (PLOT_H * i) / 3))

const path = (s: ChartSeries): string => s.values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i)} ${y(v)}`).join('')

const hover = ref<number | null>(null)

/** 指针落在哪一天。用 `getBoundingClientRect` 换算而不是 `offsetX`：SVG 可能被 CSS
 *  缩放过，`offsetX` 给的是屏幕坐标，直接当画布坐标用会偏。 */
function indexAt(event: MouseEvent): number {
  const rect = (event.currentTarget as SVGRectElement).ownerSVGElement?.getBoundingClientRect()
  if (!rect || rect.width === 0 || props.xLabels.length === 0) return 0
  const canvasX = ((event.clientX - rect.left) / rect.width) * VIEW_W
  const raw = step.value > 0 ? Math.round((canvasX - PAD_LEFT) / step.value) : 0
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
</script>

<template>
  <div class="alc">
    <div class="alc__head">
      <span class="alc__title t-eyebrow-read">{{ title }}</span>
      <span class="alc__legend">
        <span v-for="s in series" :key="s.name" class="alc__legend-item">
          <!-- 图例里的线样和图上那条线是同一套参数（颜色 + 4 3 虚线），换了线型的人
               不会在两处看到两种画法。 -->
          <svg class="alc__swatch" width="16" height="4" viewBox="0 0 16 4" aria-hidden="true">
            <line
              x1="0"
              y1="2"
              x2="16"
              y2="2"
              stroke-width="2"
              :style="{ stroke: SERIES_COLOR[s.style] }"
              :stroke-dasharray="s.style === 'dashed' ? '4 3' : undefined"
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
      <svg class="alc__plot" :viewBox="`0 0 ${VIEW_W} ${VIEW_H}`" aria-hidden="true">
        <line
          v-for="(gy, i) in gridLines"
          :key="`grid-${i}`"
          class="alc__grid"
          :x1="PAD_LEFT"
          :x2="VIEW_W - PAD_RIGHT"
          :y1="gy"
          :y2="gy"
        />
        <line
          class="alc__baseline"
          :x1="PAD_LEFT"
          :x2="VIEW_W - PAD_RIGHT"
          :y1="PAD_TOP + PLOT_H"
          :y2="PAD_TOP + PLOT_H"
        />
        <path
          v-for="s in series"
          :key="s.name"
          class="alc__line"
          :d="path(s)"
          :style="{ stroke: SERIES_COLOR[s.style] }"
          :stroke-dasharray="s.style === 'dashed' ? '4 3' : undefined"
        />
        <!-- 标记：实线系列是实心圆，虚线系列是空心圆（§7.5）。空心那一种用卡片的底色
             填，而不是 `fill: none` —— 后者会让线从洞里穿过去，看起来还是实心的。 -->
        <template v-for="s in series" :key="`pt-${s.name}`">
          <circle
            v-for="(v, i) in s.values"
            :key="i"
            class="alc__point"
            :cx="x(i)"
            :cy="y(v)"
            r="2"
            :stroke-width="s.style === 'dashed' ? 2 : 0"
            :style="{
              fill: s.style === 'dashed' ? 'var(--surface)' : SERIES_COLOR[s.style],
              stroke: SERIES_COLOR[s.style],
            }"
          />
        </template>
        <line
          v-if="hover !== null"
          class="alc__hover"
          :x1="x(hover)"
          :x2="x(hover)"
          :y1="PAD_TOP"
          :y2="PAD_TOP + PLOT_H"
        />
        <text class="alc__ink" :x="PAD_LEFT" :y="VIEW_H - 4" text-anchor="start">{{ xLabels[0] }}</text>
        <text class="alc__ink" :x="VIEW_W - PAD_RIGHT" :y="VIEW_H - 4" text-anchor="end">
          {{ xLabels[xLabels.length - 1] }}
        </text>
        <text class="alc__ink" :x="PAD_LEFT - 4" :y="PAD_TOP + 4" text-anchor="end">{{ maxValue }}</text>
        <text class="alc__ink" :x="PAD_LEFT - 4" :y="PAD_TOP + PLOT_H" text-anchor="end">0</text>
        <!-- 整块绘图区是一张点击面，放在最后（在最上层才收得到指针）。它同时管 hover
             竖线：竖线本身是 1px、收不到指针，只能由这一层算。 -->
        <rect
          class="alc__hit"
          x="0"
          y="0"
          :width="VIEW_W"
          :height="VIEW_H"
          fill="none"
          pointer-events="all"
          @mousemove="onMove"
          @mouseleave="onLeave"
          @click="onClick"
        />
      </svg>

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
  justify-content: space-between;
  gap: 8px;
}

.alc__legend {
  display: flex;
  align-items: center;
  gap: 12px;
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

.alc__plot {
  display: block;
  width: 100%;
  height: auto;
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
