<script setup lang="ts">
// 侧栏里挂在频道下面的一个任务。比频道的字往里缩一点，左边一条竖线从频道的 # 下面
// 一直连到「全部任务」，说「这几条是那个频道里的」；字和频道一样大。在等**你**的亮
// 一颗暖色点（服务端按看的人算的 `awaits_me`，不是「在待处理那一列」），正在运行的
// 一颗绿点，别的不画。
import type { RoomTask } from '@/cx_types'

import { computed } from 'vue'

import TopicRailBadge from './TopicRailBadge.vue'

import { t } from '@/i18n'
import { taskTitle } from '@/lib/topicState'

const props = withDefaults(
  defineProps<{
    task: Pick<RoomTask, 'id' | 'room_id' | 'title' | 'title_source' | 'presentation' | 'awaits_me'>
    selected: boolean
    /** 任务里别人说了几句我还没读（只对负责人和协作者算，只算人说的）。 */
    unread?: number
    // 它的频道在树里的第几层：竖线跟着频道的图标列走。
    depth?: number
  }>(),
  { depth: 0, unread: 0 }
)

const emit = defineEmits<{
  (e: 'select', task: { roomId: string; taskId: string }): void
}>()

const mark = computed<'needs-you' | 'running' | null>(() => {
  if (props.task.awaits_me) return 'needs-you'
  if (props.task.presentation.phrase === 'running') return 'running'
  return null
})
</script>

<template>
  <button
    type="button"
    class="rail-task"
    :class="{ 'rail-task--selected': selected }"
    :style="{ paddingInlineStart: 40 + depth * 20 + 'px', '--guide-x': 16 + depth * 20 + 'px' }"
    :aria-current="selected ? 'page' : undefined"
    @click="emit('select', { roomId: task.room_id, taskId: task.id })"
  >
    <!-- 任务名是人起的（占位名除外，那是界面上的字）。 -->
    <span
      class="rail-task__title"
      :class="{ 'rail-task__title--unread': unread > 0 }"
      :data-user-content="task.title_source === 'placeholder' ? undefined : ''"
      >{{ taskTitle(task) }}</span
    >
    <TopicRailBadge v-if="unread > 0" :count="unread" />
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
  position: relative;
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
  font-family: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.rail-task:hover {
  background: var(--fill-2);
  color: var(--ink);
}
/* 选中沿用频道行那一档：底色 --line-2，标题再重一层。这条 rail 的底是 --canvas，
   --fill 在它上面只有 1.027:1，拿它当选中等于没画（同 TopicRailRow.vue 那段注释）。
   字重落在行上、由标题继承：未读那颗 650 比它重，选中一行未读的任务仍是未读的样子。
   选中的行不复用 hover 档——否则鼠标一扫过，选中态反而变浅。 */
.rail-task--selected,
.rail-task--selected:hover {
  background: var(--line-2);
  color: var(--ink);
  font-weight: 600;
}
/* 竖线落在频道 # 的中线上，一行接一行连成一条（上下各探 3px 盖住行间的空隙）。
   结构线，不是强调条。 */
.rail-task::before {
  content: '';
  position: absolute;
  left: var(--guide-x);
  top: -3px;
  bottom: -3px;
  width: 1px;
  background: var(--line-2);
}
.rail-task__title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rail-task__title--unread {
  font-weight: 650;
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
