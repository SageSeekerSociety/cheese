<script setup lang="ts">
// 频道主线上的一件任务：标题、谁负责、此刻到哪一步、上一次变化是什么时候。点它打开任务。
// 同一张卡在原处变化（lib/channelTasks），状态那一格是 `TaskStateMark`（lib/taskState）。和支线长成同一块：单独成块时底色和支线一样，
// 多一行这件任务里最后说的一句；`inList` 时是消息下面那一块里的一行；`head` 时是支线
// 转成任务后，支线那一块顶上的任务名那一行。
import type { TaskLine } from '../../lib/channelTasks'

import { computed } from 'vue'

import { relTime } from '../../lib/relTime'

import UserAvatar from '@/components/common/UserAvatar.vue'
import TaskStateMark from '@/components/task/TaskStateMark.vue'
import { t } from '@/i18n'

const props = defineProps<{
  task: TaskLine
  /** 负责人的名字（按房间里的叫法）。 */
  ownerName: string | null
  /** 负责人的头像；不给就只写名字。 */
  ownerAvatar?: string | null
  /** 是消息下面那一块里的一行，不单独画底。 */
  inList?: boolean
  /** 那一块里只有它：照单独成块那样多写最后一句。 */
  lone?: boolean
  /** 支线那一块顶上的任务名：回复数、时间和最后一句在下面的支线那几行里。 */
  head?: boolean
}>()

const emit = defineEmits<{ (e: 'open', taskId: string): void }>()

// 第二行：状态有话要说（在做什么、在等什么、卡在哪、待谁审阅）就写那一句，每一种排法
// 都写；没有时，单独成块的卡写这件任务里最后说的一句。支线顶上那一行两样都不写，它们
// 在下面的支线那几行里。
const second = computed(() => {
  if (props.head) return ''
  if (props.task.state.line) return props.task.state.line
  return !props.inList || props.lone ? props.task.latest ?? '' : ''
})
</script>

<template>
  <button
    type="button"
    class="task-card"
    :class="{
      'task-card--row': inList || head,
      'task-card--head': head,
      'task-card--mine': task.mine && !inList && !head,
      'task-card--crowded': !!task.accepted,
    }"
    data-testid="task-card"
    :data-task-id="task.id"
    @click="emit('open', task.id)"
  >
    <span class="task-card__main">
      <v-icon size="16" class="task-card__icon" aria-hidden="true">mdi-checkbox-marked-outline</v-icon>
      <span class="task-card__title">{{ task.title }}</span>
      <span v-if="ownerName" class="task-card__owner">
        <UserAvatar v-if="ownerAvatar !== undefined" :size="18" :name="ownerName" :avatar="ownerAvatar ?? undefined" />
        {{ t('work.room.taskCard.owner', { name: ownerName }) }}
      </span>
      <span class="task-card__gap" />
      <span v-if="task.accepted" class="task-card__accepted">{{ task.accepted }}</span>
      <TaskStateMark
        class="task-card__status"
        data-testid="task-card-status"
        :state="task.state"
        :emphasis="task.mine"
        :size="15"
      />
      <span v-if="!head" class="task-card__at t-meta">{{ relTime(task.at) }}</span>
    </span>
    <span v-if="second" class="task-card__latest" data-testid="task-card-latest">{{ second }}</span>
  </button>
</template>

<style scoped>
.task-card {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 2px;
  width: 100%;
  max-width: 620px;
  min-width: 0;
  padding: 8px 12px;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background: var(--fill);
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.task-card:hover {
  background: var(--line-2);
}
.task-card__main {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
/* 第二行：和标题对齐（图标 16 + 间距 10）。 */
.task-card__latest {
  margin-left: 26px;
  overflow: hidden;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-card--row {
  max-width: none;
  border: 0;
  border-radius: 0;
  background: transparent;
}
.task-card--row + .task-card--row {
  border-top: 1px solid var(--line);
}
/* 支线那一块顶上：和下面的回复行是一段对话，一起变色（ChatTimeline 的 msg-work__talk）。 */
.task-card--head {
  padding-bottom: 2px;
}
.task-card--head:hover {
  background: transparent;
}
.task-card--mine {
  border-color: var(--warn);
}
.task-card__icon {
  flex: none;
  color: var(--muted);
}
.task-card__title {
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-card__owner {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.task-card__owner,
.task-card__accepted {
  flex: none;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
}
.task-card__gap {
  flex: 1;
}
.task-card__status {
  flex: none;
  font-size: 13px;
  line-height: var(--lh-13);
}
.task-card__at {
  flex: none;
}
@media (max-width: 600px) {
  .task-card__main {
    flex-wrap: wrap;
    row-gap: 2px;
  }
  .task-card__title {
    flex: 1 1 calc(100% - 30px);
  }
  .task-card__owner {
    margin-left: 26px;
  }
  /* 窄屏上一行放不下「已采纳 N 次」和时间时，先省掉时间。 */
  .task-card--crowded .task-card__at {
    display: none;
  }
}
</style>
