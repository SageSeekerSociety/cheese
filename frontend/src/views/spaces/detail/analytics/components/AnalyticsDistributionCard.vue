<template>
  <v-card flat rounded="lg" class="distribution-card">
    <div class="section-heading">
      <div>
        <h3>{{ title }}</h3>
      </div>
    </div>

    <div v-if="rows.length" class="distribution-list">
      <div v-for="row in rows" :key="row.label" class="distribution-row">
        <div class="distribution-row__meta">
          <span class="distribution-row__label">{{ row.label }}</span>
          <span class="distribution-row__value">{{ row.count }} · {{ row.percentText }}</span>
        </div>
        <div class="distribution-row__track">
          <div class="distribution-row__fill" :style="{ width: `${row.percent}%` }"></div>
        </div>
      </div>
    </div>

    <BaseEmptyState v-else size="inline" :title="t('spaces.analytics.chart.noDistribution')" />
  </v-card>
</template>

<script setup lang="ts">
import type { AnalyticsDistribution } from '@/network/api/spaces/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'

const props = defineProps<{
  title: string
  distribution?: AnalyticsDistribution | null
}>()

const { t } = useI18n()

const rows = computed(() => {
  const items = props.distribution?.items || []
  const total = items.reduce((sum, item) => sum + item.count, 0)

  return items
    .map((item) => {
      const percent =
        item.percentage != null
          ? Number(item.percentage) * (item.percentage <= 1 ? 100 : 1)
          : total > 0
            ? (item.count / total) * 100
            : 0
      return {
        label: item.label,
        count: item.count,
        percent,
        percentText: `${percent.toFixed(1)}%`,
      }
    })
    .sort((a, b) => b.count - a.count)
    .slice(0, 8)
})
</script>

<style scoped>
.distribution-card {
  height: 100%;
  padding: 16px;
  border: 1px solid var(--line);
  background: var(--surface);
}

.section-heading {
  margin-bottom: 16px;
}

h3 {
  margin: 0;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}

.distribution-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.distribution-row__meta {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  margin-bottom: 6px;
  font-size: 13px;
  line-height: var(--lh-13);
}

.distribution-row__label {
  color: var(--text);
}

.distribution-row__value {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

.distribution-row__track {
  overflow: hidden;
  height: 6px;
  border-radius: var(--radius-pill);
  background: var(--fill-2);
}

.distribution-row__fill {
  height: 100%;
  border-radius: inherit;
  background: var(--muted);
}
</style>
