<script setup lang="ts">
// 执行记录里的一行：什么时候跑的、结果在哪。面板里那一格铺最近 5 次，整页上铺一页
// （10 行）—— 同一行东西，所以抽在这里，两边画的是同一份。
//
// 一行只有两种样子：跑成了写结果文件；没跑成写「未能执行」，原因折在「▸ 原因」里
// （原生 <details>，键盘和读屏都认）。还在跑的那一次写状态本身 —— 它没有结果可说。
import type { NavTarget } from '@/lib/navTarget'
import type { RoutineRun } from '@/lib/routine'

import NavLink from '../common/NavLink.vue'

import { t } from '@/i18n'
import { routineMoment, routineRunLabel } from '@/lib/routine'

const props = withDefaults(
  defineProps<{
    run: RoutineRun
    /** 这条规则自己的时区：那个时刻按它写（和卡片上「每天 18:00（北京时间）」同一个）。 */
    timezone?: string
    /** 结果文件那一行的去处。项目总览页和整页给（那两处没有能把文件摊开的工作面板，
     *  去了频道才点得开）；房间右侧那一格不给，那里的每一份都当场点开。 */
    roomTo?: NavTarget
  }>(),
  { timezone: undefined, roomTo: undefined }
)

const emit = defineEmits<{
  /** 点了某次执行交回的一份结果文件：打开它。 */
  (e: 'open-file', path: string): void
}>()

/** 成了或者还在跑：这一行写的是「它交回了什么」。 */
const tells = (r: RoutineRun) => r.status === 'succeeded' || r.status === 'running' || r.status === 'queued'
</script>

<template>
  <div class="routine-run__head t-meta">
    <span class="routine-run__when">{{ routineMoment(run.started_at || run.created_at, props.timezone) }}</span>
    <span class="routine-run__sep" aria-hidden="true">|</span>
    <template v-if="run.outputs.length">
      <template v-for="path in run.outputs" :key="path">
        <NavLink v-if="roomTo" :to="roomTo" class="routine-run__file">{{ path }}</NavLink>
        <button v-else type="button" class="routine-run__file" @click="emit('open-file', path)">
          {{ path }}
        </button>
      </template>
    </template>
    <template v-else-if="tells(run)">
      <span class="c-faint">{{ routineRunLabel(run.status) }}</span>
    </template>
    <template v-else>
      <span class="c-danger">{{ routineRunLabel(run.status) }}</span>
      <!-- 原因折着：点「▸ 原因」才展开。 -->
      <details v-if="run.error" class="routine-run__reason">
        <summary class="routine-run__summary">
          <span class="routine-run__chev" aria-hidden="true">▸</span>
          {{ t('routines.runs.reason') }}
        </summary>
        <p class="t-body c-muted routine-run__why">{{ run.error }}</p>
      </details>
      <span v-else class="c-faint">{{ t('routines.noReason') }}</span>
    </template>
  </div>
</template>

<style scoped>
.routine-run__head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px 8px;
}
.routine-run__when {
  color: var(--faint);
  font-variant-numeric: tabular-nums;
}
.routine-run__sep {
  color: var(--line);
}
.routine-run__reason {
  display: inline;
}
.routine-run__summary {
  display: inline;
  cursor: pointer;
  list-style: none;
  color: var(--muted);
}
.routine-run__summary::-webkit-details-marker {
  display: none;
}
.routine-run__chev {
  display: inline-block;
  transition: transform var(--dur-quick) var(--ease-standard);
}
.routine-run__reason[open] .routine-run__chev {
  transform: rotate(90deg);
}
.routine-run__why {
  margin: 4px 0 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
/* 一份结果文件：一颗文字按钮，点开它就打开了文件。下划线是说这件事，不是装饰。 */
.routine-run__file {
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
  overflow-wrap: anywhere;
  text-align: start;
}
.routine-run__file:hover {
  color: var(--ink);
}
</style>
