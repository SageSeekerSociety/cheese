<script setup lang="ts">
// 侧栏里挂在房间下面的一个任务。比房间那一行缩进一级、字小一号；需要你处理的亮
// 一颗暖色点，正在运行的一颗绿点，别的不画。
import type { RoomTask } from '@/cx_types'

import { computed } from 'vue'

import { t } from '@/i18n'
import { taskTitle } from '@/lib/topicState'

const props = defineProps<{
  task: Pick<RoomTask, 'id' | 'room_id' | 'title' | 'title_source' | 'presentation'>
  selected: boolean
}>()

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
    class="rail-task t-meta"
    :class="{ 'rail-task--selected': selected }"
    :aria-current="selected ? 'page' : undefined"
    @click="emit('select', { roomId: task.room_id, taskId: task.id })"
  >
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
  gap: 8px;
  width: 100%;
  min-height: 28px;
  padding: 0 12px 0 32px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--muted);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.rail-task:hover {
  background: var(--fill);
  color: var(--text);
}
.rail-task--selected {
  background: var(--fill);
  color: var(--ink);
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
