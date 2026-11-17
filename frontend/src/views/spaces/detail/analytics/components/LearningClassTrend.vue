<template>
  <section class="an-card">
    <h3 class="an-card__title">{{ t('spaces.analytics.learning.class.trend.title') }}</h3>
    <p v-if="!hasAny" class="an-note">{{ t('spaces.analytics.learning.class.trend.empty') }}</p>
    <svg
      v-else
      class="lc-trend"
      :viewBox="`0 0 ${W} ${H}`"
      role="img"
      :aria-label="t('spaces.analytics.learning.class.trend.title')"
    >
      <g v-for="tick in ticks" :key="tick">
        <line class="lc-trend__grid" :x1="L" :x2="W - R" :y1="y(tick)" :y2="y(tick)" />
        <text class="lc-trend__label" :x="L - 6" :y="y(tick) + 4" text-anchor="end">{{ tick }}</text>
      </g>
      <text
        v-for="(week, index) in data.weeks"
        :key="week"
        class="lc-trend__label"
        :x="x(index)"
        :y="H - 10"
        text-anchor="middle"
      >
        {{ week.slice(5) }}
      </text>
      <g
        v-for="(series, index) in shown"
        :key="series.knowledgePointId"
        :style="{ color: `var(--chart-${(index % 8) + 1})` }"
      >
        <polyline
          v-for="(segment, part) in segments(series.values)"
          :key="part"
          class="lc-trend__line"
          :points="segment"
        />
        <template v-for="(value, week) in series.values" :key="week">
          <circle v-if="value != null" class="lc-trend__dot" :cx="x(week)" :cy="y(value)" r="3" />
        </template>
        <text class="lc-trend__name" :x="W - R + 8" :y="lastY(series.values) + 4">{{ series.name }}</text>
      </g>
    </svg>
    <p class="an-note lc-gap">{{ t('spaces.analytics.learning.class.trend.gap') }}</p>
  </section>
</template>

<script setup lang="ts">
// 趋势：各知识点每周卡住的人数（每晚写的按周快照）。不足 3 人的那一周是空点，线在那里断开。
import type { LearningClassTrend } from '@/types/apiSpaces'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{ data: LearningClassTrend }>()
const { t } = useI18n()

const W = 820
const H = 280
const L = 40
const R = 170
const T = 14
const B = 34

const shown = computed(() => props.data.series.filter((series) => series.values.some((value) => value != null)))
const hasAny = computed(() => shown.value.length > 0)
const top = computed(() => Math.max(3, ...shown.value.flatMap((series) => series.values.map((value) => value ?? 0))))
const ticks = computed(() => {
  const step = Math.max(1, Math.ceil(top.value / 3))
  return [0, step, step * 2, step * 3]
})
const span = computed(() => ticks.value[ticks.value.length - 1] || 1)
const x = (index: number) => L + (index * (W - L - R)) / Math.max(1, props.data.weeks.length - 1)
const y = (value: number) => T + (1 - value / span.value) * (H - T - B)

/** 连续有数的几周连成一段；空的那一周断开。 */
const segments = (values: Array<number | null>) => {
  const out: string[] = []
  let current: string[] = []
  values.forEach((value, index) => {
    if (value == null) {
      if (current.length > 1) out.push(current.join(' '))
      current = []
      return
    }
    current.push(`${x(index)},${y(value)}`)
  })
  if (current.length > 1) out.push(current.join(' '))
  return out
}

const lastY = (values: Array<number | null>) => {
  const last = [...values].reverse().find((value) => value != null)
  return y(last ?? 0)
}
</script>

<style scoped src="../analytics.css"></style>
<style scoped src="./learningClass.css"></style>

<style scoped>
.lc-trend {
  width: 100%;
  height: auto;
}

.lc-trend__grid {
  stroke: var(--line);
}

.lc-trend__label {
  fill: var(--muted);
  font-size: 13px;
}

.lc-trend__line {
  fill: none;
  stroke: currentcolor;
  stroke-width: 2.5;
}

.lc-trend__dot {
  fill: currentcolor;
}

.lc-trend__name {
  fill: currentcolor;
  font-size: 13px;
}
</style>
