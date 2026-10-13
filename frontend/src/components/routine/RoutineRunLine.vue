<script setup lang="ts">
// 例行任务那条消息下面的一行：哪条规则、跑得怎么样、结果落进了哪几份文件，以及去看
// 这次执行的支线。成了，AI 队友把结果写在消息本身里，这一行只写文件落在哪儿；没跑成，
// 队友那天什么也没交出来，消息是空的，这一行的「未能执行」就是这次的全部结论 —— 原因
// 折在它下面，谁想知道才点开。
import type { RoutineRunLine } from '@/types/routineRun'

import { computed } from 'vue'

import { t } from '@/i18n'
import { routineRunLabel } from '@/lib/routine'

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

// 这次执行交回的结果文件。例行事务里填的「存储位置 周报/」只是它们在频道文件区里的落脚
// 路径 —— 频道面板上没有对应的文件夹，这一行是它们唯一的入口，所以每一份都得点得开，
// 写的也是整条路径（点开之前就知道是哪个「周报/」）。
const files = computed(() => (going.value || missed.value ? [] : props.run.outputs))
</script>

<template>
  <div class="run-line t-meta" :class="{ 'run-line--missed': missed }" data-testid="routine-run-line">
    <span class="run-line__chip t-meta">{{ t('routines.line.chip', { title: run.title }) }}</span>
    <span v-if="going" class="t-meta c-muted" data-testid="routine-run-going">
      {{ routineRunLabel(run.status) }}
    </span>
    <!-- 没跑成时这一行只写「未能执行」，原因是平台给的、可能有好几行长：这一行是消息下面
         的一行小字，铺开会把消息本身顶下去，所以折起来（原生 <details>，键盘和读屏都认）。 -->
    <template v-else-if="missed">
      <span class="run-line__missed t-meta" data-testid="routine-run-missed">
        {{ t('routines.line.missed') }}
      </span>
      <details v-if="run.reason" class="run-line__why">
        <summary class="run-line__summary">
          <span class="run-line__chev" aria-hidden="true">▸</span>
          {{ t('routines.line.missedReason') }}
        </summary>
        <p class="t-body c-muted run-line__why-body">{{ run.reason }}</p>
      </details>
      <span v-else class="t-meta c-faint">{{ t('routines.noReason') }}</span>
    </template>
    <span v-if="files.length" class="run-line__files" data-testid="routine-run-files">
      <span class="t-meta c-faint">{{ t('routines.line.files') }}</span>
      <button
        v-for="path in files"
        :key="path"
        type="button"
        class="run-line__file"
        @click="emit('open-file', path)"
      >
        {{ path }}
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
.run-line__missed {
  color: var(--danger-ink);
}
/* 「▸ 未能执行的原因」另起一行：折着的时候它是一行小字，展开时平台那段原因要占满整行。 */
.run-line__why {
  flex-basis: 100%;
}
.run-line__summary {
  cursor: pointer;
  list-style: none;
  color: var(--muted);
}
.run-line__summary::-webkit-details-marker {
  display: none;
}
.run-line__chev {
  display: inline-block;
  transition: transform var(--dur-quick) var(--ease-standard);
}
.run-line__why[open] .run-line__chev {
  transform: rotate(90deg);
}
.run-line__why-body {
  margin: 4px 0 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
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
