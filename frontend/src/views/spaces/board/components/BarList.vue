<script setup lang="ts">
// 横向排行条。看板里「哪几道题最热」「谁出的题被领得最多」都用它。
//
// 选横向而不是竖向柱：这一屏的分类名和题目名是中文长串，竖向柱的轴标签要斜着写才放得下。
import { computed } from 'vue'

const props = defineProps<{
  rows: { label: string; value: number; hint?: string }[]
  /** 值后面跟的单位，如「人」。 */
  unit?: string
  /** 空态文案。 */
  empty?: string
}>()

const max = computed(() => Math.max(1, ...props.rows.map((r) => r.value)))
</script>

<template>
  <div v-if="rows.length" class="bars">
    <div v-for="(row, i) in rows" :key="row.label" class="bars__row">
      <span class="bars__rank">{{ i + 1 }}</span>
      <span class="bars__label" :title="row.label">{{ row.label }}</span>
      <span class="bars__track">
        <i :style="{ width: `${Math.max(3, (row.value / max) * 100)}%` }" />
      </span>
      <span class="bars__value">{{ row.value }}{{ unit ?? '' }}</span>
    </div>
    <p v-if="rows.some((r) => r.hint)" class="bars__foot">
      <span v-for="r in rows.filter((x) => x.hint)" :key="r.label">{{ r.label }}：{{ r.hint }}</span>
    </p>
  </div>
  <v-empty-state v-else icon="mdi-chart-bar" :title="empty ?? '暂无数据'" />
</template>

<style scoped lang="scss">
.bars {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.bars__row {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) minmax(80px, 1.4fr) 48px;
  gap: 10px;
  align-items: center;
}

.bars__rank {
  color: rgba(var(--v-theme-on-surface), 0.35);
  font-size: 0.72rem;
  font-variant-numeric: tabular-nums;
}

.bars__label {
  overflow: hidden;
  font-size: 0.82rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bars__track {
  height: 8px;
  overflow: hidden;
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: 999px;
}

.bars__track i {
  display: block;
  height: 100%;
  background: rgba(var(--v-theme-on-surface), 0.45);
  border-radius: 999px;
}

.bars__value {
  text-align: right;
  font-size: 0.82rem;
  font-variant-numeric: tabular-nums;
}

.bars__foot {
  display: flex;
  flex-wrap: wrap;
  gap: 0 12px;
  margin: 4px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.72rem;
}
</style>
