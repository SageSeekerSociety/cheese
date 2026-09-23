<script setup lang="ts">
// 一条按比例分段的横条 + 图例。用在「题目状态构成」「完成情况」这类占比上。
//
// 不用饼图：这里的段只有两到四段，饼图读不出的「谁比谁多一点点」横条一眼就能看出，
// 而且横条能在窄屏上顺着排版走。
import { computed } from 'vue'

const props = defineProps<{
  segments: { label: string; count: number; tone?: 'ok' | 'warn' | 'danger' | 'muted' | 'primary' }[]
}>()

const total = computed(() => props.segments.reduce((n, s) => n + s.count, 0))

const TONE: Record<string, string> = {
  ok: 'rgb(var(--v-theme-success))',
  warn: 'rgb(var(--v-theme-warning))',
  danger: 'rgb(var(--v-theme-error))',
  primary: 'rgb(var(--v-theme-primary))',
  muted: 'rgba(120,126,134,0.55)',
}
</script>

<template>
  <div class="split">
    <div class="split__bar">
      <i
        v-for="s in segments"
        v-show="s.count > 0"
        :key="s.label"
        :style="{ width: `${total ? (s.count / total) * 100 : 0}%`, background: TONE[s.tone ?? 'muted'] }"
        :title="`${s.label} ${s.count}`"
      />
    </div>
    <ul class="split__legend">
      <li v-for="s in segments" :key="s.label">
        <i :style="{ background: TONE[s.tone ?? 'muted'] }" />
        <span class="split__name">{{ s.label }}</span>
        <span class="split__num">{{ s.count }}</span>
        <span class="split__pct">{{ total ? Math.round((s.count / total) * 100) : 0 }}%</span>
      </li>
    </ul>
  </div>
</template>

<style scoped lang="scss">
.split__bar {
  display: flex;
  height: 10px;
  overflow: hidden;
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: 999px;
}

.split__bar i {
  height: 100%;
}

.split__legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  padding: 0;
  margin: 12px 0 0;
  list-style: none;
}

.split__legend li {
  display: inline-flex;
  gap: 7px;
  align-items: center;
  font-size: 0.8rem;
}

.split__legend i {
  width: 8px;
  height: 8px;
  border-radius: 2px;
}

.split__name {
  color: rgba(var(--v-theme-on-surface), 0.66);
}

.split__num {
  font-weight: 600;
}

.split__pct {
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-variant-numeric: tabular-nums;
}
</style>
