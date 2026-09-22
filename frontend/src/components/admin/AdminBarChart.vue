<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

// 看板上的柱状图（§6.3 第 14 项「按天新增成员」这一类只有一个量、按天分桶的数）。
//
// 和 `AdminLineChart` 共用同一份配色规则（§7.5）：**一个量就用一档中性色**（`--text`），
// 网格 `--line`，轴标签 `--muted` 12px。这里没有第二条柱子，所以没有线型/色相要分 ——
// 也正因如此不能给它编一个颜色：琥珀留给主操作（§7.4），随手上一个色相就多一处
// 「这是不是能点」的误读。§10.2 提到的 `--chart-1` / `--chart-2` 已经删掉了（F-04）。
//
// 手写 SVG，不引图表库，理由同 `AdminLineChart`。坐标按设计尺寸 628×180 算再等比缩放。

const props = withDefaults(
  defineProps<{
    title: string
    rows: { label: string; value: number }[]
    loading?: boolean
  }>(),
  { loading: false }
)

const { t } = useI18n()

const VIEW_W = 628
const VIEW_H = 180
const PAD_LEFT = 32
const PAD_RIGHT = 8
const PAD_TOP = 8
const PAD_BOTTOM = 20
const PLOT_W = VIEW_W - PAD_LEFT - PAD_RIGHT
const PLOT_H = VIEW_H - PAD_TOP - PAD_BOTTOM

/** 柱子最宽 32px。再宽就成色块了 —— 柱状图靠空隙分组，不靠颜色。 */
const MAX_BAR_W = 32
/** 每根柱子底下都写标签的上限。超过这个数，标签会互相压成一团黑，那时宁可不写。 */
const LABEL_LIMIT = 12

const empty = computed(() => props.rows.length === 0)

const maxValue = computed(() => Math.max(1, ...props.rows.map((r) => r.value)))

const slot = computed(() => (props.rows.length > 0 ? PLOT_W / props.rows.length : PLOT_W))

const barW = computed(() => Math.min(MAX_BAR_W, slot.value * 0.6))

const centerX = (i: number): number => PAD_LEFT + slot.value * (i + 0.5)

const barH = (v: number): number => (v / maxValue.value) * PLOT_H

const barY = (v: number): number => PAD_TOP + PLOT_H - barH(v)

const gridLines = computed(() => [0, 1, 2, 3].map((i) => PAD_TOP + (PLOT_H * i) / 3))
</script>

<template>
  <div class="abc">
    <div class="abc__head">
      <span class="abc__title t-eyebrow-read">{{ title }}</span>
    </div>

    <div v-if="loading" class="abc__skeleton">
      <v-skeleton-loader type="text" class="abc__skel abc__skel--title" />
      <v-skeleton-loader type="image" class="abc__skel abc__skel--plot" />
    </div>

    <p v-else-if="empty" class="abc__none">
      <span class="abc__none-title">{{ t('feedback.dashboard.empty.title') }}</span>
      <span class="abc__none-desc">{{ t('feedback.dashboard.empty.desc') }}</span>
    </p>

    <template v-else>
      <svg class="abc__plot" :viewBox="`0 0 ${VIEW_W} ${VIEW_H}`" aria-hidden="true">
        <line
          v-for="(gy, i) in gridLines"
          :key="`grid-${i}`"
          class="abc__grid"
          :x1="PAD_LEFT"
          :x2="VIEW_W - PAD_RIGHT"
          :y1="gy"
          :y2="gy"
        />
        <rect
          v-for="(row, i) in rows"
          :key="i"
          class="abc__bar"
          :x="centerX(i) - barW / 2"
          :y="barY(row.value)"
          :width="barW"
          :height="barH(row.value)"
        />
        <text class="abc__ink" :x="PAD_LEFT - 4" :y="PAD_TOP + 4" text-anchor="end">{{ maxValue }}</text>
        <text class="abc__ink" :x="PAD_LEFT - 4" :y="PAD_TOP + PLOT_H" text-anchor="end">0</text>
        <template v-if="rows.length <= LABEL_LIMIT">
          <text
            v-for="(row, i) in rows"
            :key="`label-${i}`"
            class="abc__ink"
            :x="centerX(i)"
            :y="VIEW_H - 4"
            text-anchor="middle"
          >
            {{ row.label }}
          </text>
        </template>
      </svg>

      <details class="abc__data">
        <summary class="abc__toggle">{{ t('feedback.chart.dataTable') }}</summary>
        <table class="abc__table">
          <thead>
            <tr>
              <th scope="col">{{ t('feedback.chart.date') }}</th>
              <th scope="col">{{ title }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in rows" :key="i">
              <th scope="row" class="abc__rowhead">{{ row.label }}</th>
              <td class="abc__cell t-num">{{ row.value }}</td>
            </tr>
          </tbody>
        </table>
      </details>
    </template>
  </div>
</template>

<style scoped>
.abc {
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

/* 轴标签与图例共用一个类，同 `AdminLineChart`：`fill` 取 `currentColor`，颜色只有一个
   来源（`color`），SVG 文字不认 `color` 这件事只在这里处理一次。 */
.abc__ink {
  font-size: 12px;
  color: var(--muted);
  fill: currentColor;
}

.abc__plot {
  display: block;
  width: 100%;
  height: auto;
}

.abc__grid {
  stroke: var(--line);
  stroke-width: 1;
}

.abc__bar {
  fill: var(--text);
}

.abc__toggle {
  color: var(--muted);
  font-size: 12.5px;
  line-height: var(--lh-12);
  cursor: pointer;
}

.abc__table {
  width: 100%;
  margin-top: 8px;
  border-collapse: collapse;
  font-size: 12.5px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.abc__table th,
.abc__table td {
  padding: 4px 8px;
  text-align: right;
  border-bottom: 1px solid var(--line);
}

.abc__table th:first-child,
.abc__table td:first-child {
  text-align: left;
}

.abc__table tr:last-child th,
.abc__table tr:last-child td {
  border-bottom: 0;
}

.abc__rowhead {
  font-weight: 400;
}

/* 空态照 §9.2 那一套（主 15/600/--ink，副 13/--muted，中间 8px）。 */
.abc__none {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
}

.abc__none-title {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
}

.abc__none-desc {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.abc__skel--title {
  width: 96px;
}

.abc__skel--plot {
  width: 100%;
}

.abc__skel :deep(.v-skeleton-loader__text) {
  height: 12px;
  margin: 0;
  background: var(--fill-2);
}

.abc__skel--plot :deep(.v-skeleton-loader__image) {
  height: 180px;
  margin: 0;
  background: var(--fill-2);
}
</style>
