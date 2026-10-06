<script setup lang="ts">
// 例行任务那条消息下面的一行：哪条规则、跑得怎么样，以及去看这次执行的支线。
// 结果写在消息本身里；没跑成时 AI 队友不替平台说话，原因画在这一行。
import type { RoutineRunLine } from '@/types/routineRun'

import { computed } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{
  run: RoutineRunLine
  /** 消息下面已经有支线那一行了：就不再给「查看这次运行」。 */
  threaded: boolean
}>()

const emit = defineEmits<{ (e: 'open'): void }>()

const going = computed(() => props.run.status === 'queued' || props.run.status === 'running')
const missed = computed(() => props.run.status === 'failed' || props.run.status === 'skipped')
const minutes = computed(() => {
  const { started_at: start, finished_at: end } = props.run
  if (!start || !end) return null
  return Math.max(1, Math.round((Date.parse(end) - Date.parse(start)) / 60_000))
})
</script>

<template>
  <div class="run-line t-meta" :class="{ 'run-line--missed': missed }" data-testid="routine-run-line">
    <span class="run-line__chip t-meta">{{ t('routines.line.chip', { title: run.title }) }}</span>
    <span v-if="going" class="t-meta c-muted" data-testid="routine-run-going">{{ t('routines.line.going') }}</span>
    <span v-else-if="missed" class="run-line__reason t-meta" data-testid="routine-run-missed">
      {{ run.reason ? t('routines.line.missedBecause', { reason: run.reason }) : t('routines.line.missed') }}
    </span>
    <span v-else-if="minutes" class="t-meta c-faint">{{ t('routines.line.took', { minutes }) }}</span>
    <button v-if="!threaded" type="button" class="run-line__open" @click="emit('open')">
      {{ t('routines.line.open') }}
    </button>
  </div>
</template>

<style scoped>
.run-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  min-width: 0;
}
.run-line__chip {
  padding: 0 8px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--muted);
}
.run-line__reason {
  color: var(--warn-ink);
}
.run-line__open {
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  color: var(--muted);
  cursor: pointer;
  transition: color var(--dur-quick) var(--ease-standard);
}
.run-line__open:hover {
  color: var(--ink);
}
</style>
