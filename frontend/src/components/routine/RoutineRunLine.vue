<script setup lang="ts">
// 例行任务那条消息下面的一行：哪条规则、跑得怎么样、结果落进了哪几份文件，以及去看
// 这次执行的支线。结果写在消息本身里；没跑成时 AI 队友不替平台说话，原因画在这一行。
import type { RoutineRunLine } from '@/types/routineRun'

import { computed } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{
  run: RoutineRunLine
  /** 消息下面已经有支线那一行了：就不再给「查看这次运行」。 */
  threaded: boolean
}>()

const emit = defineEmits<{
  (e: 'open'): void
  /** 点了这次执行交回的一份结果文件：打开它。 */
  (e: 'open-file', path: string): void
}>()

const going = computed(() => props.run.status === 'queued' || props.run.status === 'running')
const missed = computed(() => props.run.status === 'failed' || props.run.status === 'skipped')
const minutes = computed(() => {
  const { started_at: start, finished_at: end } = props.run
  if (!start || !end) return null
  return Math.max(1, Math.round((Date.parse(end) - Date.parse(start)) / 60_000))
})

// 这次执行交回的结果文件。规则里填的「结果放在 周报/」只是它们在频道文件区里的落脚
// 路径 —— 频道面板上没有对应的文件夹，这一行是它们唯一的入口，所以每一份都得点得开。
const files = computed(() => (going.value || missed.value ? [] : props.run.outputs))

function fileName(path: string): string {
  return path.split('/').pop() || path
}
</script>

<template>
  <div class="run-line t-meta" :class="{ 'run-line--missed': missed }" data-testid="routine-run-line">
    <span class="run-line__chip t-meta">{{ t('routines.line.chip', { title: run.title }) }}</span>
    <span v-if="going" class="t-meta c-muted" data-testid="routine-run-going">{{ t('routines.line.going') }}</span>
    <span v-else-if="missed" class="run-line__reason t-meta" data-testid="routine-run-missed">
      {{ run.reason ? t('routines.line.missedBecause', { reason: run.reason }) : t('routines.line.missed') }}
    </span>
    <span v-else-if="minutes" class="t-meta c-faint">{{ t('routines.line.took', { minutes }) }}</span>
    <span v-if="files.length" class="run-line__files" data-testid="routine-run-files">
      <span class="t-meta c-faint">{{ t('routines.line.files') }}</span>
      <button
        v-for="path in files"
        :key="path"
        type="button"
        class="run-line__file"
        :title="path"
        @click="emit('open-file', path)"
      >
        {{ fileName(path) }}
      </button>
    </span>
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
.run-line__files {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  min-width: 0;
}
/* 一份结果文件：和「查看这次运行」同一种东西（这一行里的一颗文字按钮），只是它开的
   是文件——下划线是说这件事，不是装饰。 */
.run-line__file {
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  color: var(--muted);
  text-decoration: underline;
  text-decoration-color: var(--line);
  text-underline-offset: 3px;
  cursor: pointer;
  transition: color var(--dur-quick) var(--ease-standard);
}
.run-line__file:hover {
  color: var(--ink);
}
</style>
