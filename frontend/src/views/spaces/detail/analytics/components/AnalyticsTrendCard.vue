<template>
  <v-card flat rounded="lg" class="trend-card">
    <div class="trend-card__header">
      <div>
        <h3>{{ title }}</h3>
      </div>
      <div class="trend-card__summary t-num">
        {{ t('spaces.analytics.chart.summary', { total, points: pointCount }) }}
      </div>
    </div>

    <div v-if="pointCount" class="trend-card__chart">
      <svg viewBox="0 0 420 180" preserveAspectRatio="none">
        <polyline class="trend-line trend-line--area" :points="areaPath" />
        <polyline class="trend-line trend-line--stroke" :points="linePath" />
      </svg>
      <div class="trend-card__footer">
        <span>{{ firstLabel }}</span>
        <span>{{ lastLabel }}</span>
      </div>
    </div>

    <BaseEmptyState v-else size="inline" :title="t('spaces.analytics.chart.noTrend')" />
  </v-card>
</template>

<script setup lang="ts">
import type { AnalyticsTimeSeriesPoint } from '@/network/api/spaces/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'

const props = defineProps<{
  title: string
  points?: AnalyticsTimeSeriesPoint[] | null
}>()

const { t, locale } = useI18n()

const normalizedPoints = computed(() => props.points || [])
const pointCount = computed(() => normalizedPoints.value.length)
const total = computed(() => normalizedPoints.value.reduce((sum, point) => sum + point.count, 0))

const projectedPoints = computed(() => {
  const points = normalizedPoints.value
  if (!points.length) return []

  const max = Math.max(...points.map((point) => point.count), 1)
  return points.map((point, index) => {
    const x = points.length === 1 ? 210 : (index / (points.length - 1)) * 400 + 10
    const y = 150 - (point.count / max) * 120
    return { x, y, point }
  })
})

const linePath = computed(() => projectedPoints.value.map(({ x, y }) => `${x},${y}`).join(' '))
const areaPath = computed(() => {
  if (!projectedPoints.value.length) return ''
  const head = projectedPoints.value[0]
  const tail = projectedPoints.value.at(-1)
  return `10,160 ${projectedPoints.value.map(({ x, y }) => `${x},${y}`).join(' ')} ${tail?.x ?? 410},160 ${head.x},160`
})

const firstLabel = computed(() => {
  const point = normalizedPoints.value[0]
  return point ? new Date(point.bucket).toLocaleDateString(locale.value) : ''
})
const lastLabel = computed(() => {
  const point = normalizedPoints.value.at(-1)
  return point ? new Date(point.bucket).toLocaleDateString(locale.value) : ''
})
</script>

<style scoped>
.trend-card {
  padding: 16px;
  border: 1px solid var(--line);
  background: var(--surface);
}

.trend-card__header {
  display: flex;
  gap: 16px;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 12px;
}

h3 {
  margin: 0;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}

.trend-card__summary,
.trend-card__footer {
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}

.trend-card__chart svg {
  width: 100%;
  height: 160px;
}

.trend-line {
  fill: none;
}

.trend-line--area {
  fill: var(--fill-2);
}

.trend-line--stroke {
  stroke: var(--muted);
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
  vector-effect: non-scaling-stroke;
}

.trend-card__footer {
  display: flex;
  justify-content: space-between;
  margin-top: 8px;
}
</style>
