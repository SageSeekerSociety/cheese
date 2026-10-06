<script setup lang="ts">
// 侧栏里挂在房间下面的一个任务（样稿「侧栏 A」）。比它的房间往里缩一级，前面一道分支
// 线说「这是那个房间里的」，字和房间一样大；需要你处理的亮一颗暖色点，正在运行的一颗
// 绿点，别的不画。
import type { RoomTask } from '@/cx_types'

import { computed } from 'vue'

import { t } from '@/i18n'
import { taskTitle } from '@/lib/topicState'

const props = withDefaults(
  defineProps<{
    task: Pick<RoomTask, 'id' | 'room_id' | 'title' | 'title_source' | 'presentation'>
    selected: boolean
    // 它的房间在树里的第几层：任务比房间再缩一级。
    depth?: number
  }>(),
  { depth: 0 }
)

const emit = defineEmits<{
  (e: 'select', task: { roomId: string; taskId: string }): void
}>()

const mark = computed<'needs-you' | 'running' | null>(() => {
  if (props.task.presentation.column === 'needs_you') return 'needs-you'
  if (props.task.presentation.phrase === 'running') return 'running'
  return null
})
</script>

<template>
  <button
    type="button"
    class="rail-task"
    :class="{ 'rail-task--selected': selected }"
    :style="{ paddingInlineStart: 30 + depth * 20 + 'px' }"
    :aria-current="selected ? 'page' : undefined"
    @click="emit('select', { roomId: task.room_id, taskId: task.id })"
  >
    <svg class="rail-task__branch" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
      <path d="M2 1.5v6.5a2.5 2.5 0 0 0 2.5 2.5H12" />
    </svg>
    <span class="rail-task__title">{{ taskTitle(task) }}</span>
    <span
      v-if="mark"
      class="rail-task__dot"
      :class="`rail-task__dot--${mark}`"
      role="img"
      :aria-label="mark === 'needs-you' ? t('work.sidebar.taskNeedsYou') : t('work.sidebar.taskRunning')"
    />
  </button>
</template>

<style scoped>
.rail-task {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-height: 32px;
  padding-block: 0;
  padding-inline-end: 12px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.rail-task:hover {
  background: var(--fill);
  color: var(--ink);
}
.rail-task--selected {
  background: var(--fill);
  color: var(--ink);
}
.rail-task__branch {
  flex: none;
  fill: none;
  stroke: var(--faint);
  stroke-width: 1.3;
}
.rail-task__title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rail-task__dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-pill);
}
.rail-task__dot--needs-you {
  background: var(--warn);
}
.rail-task__dot--running {
  background: var(--ok);
}
</style>
