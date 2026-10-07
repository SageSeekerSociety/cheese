<script setup lang="ts">
// 频道主线上的一张任务卡：标题、谁负责、此刻到哪一步、上一次变化是什么时候。点它打开任务。
// 同一张卡在原处变化（lib/channelTasks）；`inList` 时它是支线区里的一行，否则单独成卡。
import type { TaskLine } from '../../lib/channelTasks'

import { relTime } from '../../lib/relTime'

import { t } from '@/i18n'

defineProps<{
  task: TaskLine
  /** 负责人的名字（按房间里的叫法）。 */
  ownerName: string | null
  /** 是支线区里几行中的一行，不单独画边框。 */
  inList?: boolean
}>()

const emit = defineEmits<{ (e: 'open', taskId: string): void }>()
</script>

<template>
  <button
    type="button"
    class="task-card"
    :class="{
      'task-card--row': inList,
      'task-card--mine': task.tone === 'mine' && !inList,
      'task-card--crowded': !!task.accepted,
    }"
    data-testid="task-card"
    :data-task-id="task.id"
    @click="emit('open', task.id)"
  >
    <v-icon size="16" class="task-card__icon" aria-hidden="true">mdi-checkbox-marked-outline</v-icon>
    <span class="task-card__title">{{ task.title }}</span>
    <span v-if="ownerName" class="task-card__owner">{{ t('work.room.taskCard.owner', { name: ownerName }) }}</span>
    <span class="task-card__gap" />
    <span v-if="task.accepted" class="task-card__accepted">{{ task.accepted }}</span>
    <span class="task-card__status" :data-tone="task.tone" data-testid="task-card-status">
      <v-icon v-if="task.tone === 'done'" size="13" aria-hidden="true">mdi-check</v-icon>
      <v-icon v-else-if="task.tone === 'closed'" size="13" aria-hidden="true">mdi-minus</v-icon>
      <span v-else class="task-card__dot" aria-hidden="true" />
      {{ task.status }}
    </span>
    <span class="task-card__at t-meta">{{ relTime(task.at) }}</span>
  </button>
</template>

<style scoped>
.task-card {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  max-width: 620px;
  min-width: 0;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.task-card:hover {
  background: var(--fill);
}
.task-card--row {
  max-width: none;
  border: 0;
  border-radius: 0;
}
.task-card--row + .task-card--row {
  border-top: 1px solid var(--line);
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
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
}
.task-card__dot {
  width: 7px;
  height: 7px;
  border-radius: var(--radius-pill);
  background: var(--faint);
}
.task-card__status[data-tone='discussing'] .task-card__dot {
  border: 1.5px solid var(--faint);
  background: transparent;
}
.task-card__status[data-tone='running'],
.task-card__status[data-tone='done'] {
  color: var(--ok-ink);
}
.task-card__status[data-tone='running'] .task-card__dot {
  background: var(--ok);
}
.task-card__status[data-tone='waiting'],
.task-card__status[data-tone='mine'] {
  color: var(--warn-ink);
}
.task-card__status[data-tone='mine'] {
  font-weight: 600;
}
.task-card__status[data-tone='waiting'] .task-card__dot,
.task-card__status[data-tone='mine'] .task-card__dot {
  background: var(--warn);
}
.task-card__status[data-tone='stuck'] {
  color: var(--danger-ink);
}
.task-card__status[data-tone='stuck'] .task-card__dot {
  background: var(--danger);
}
.task-card__at {
  flex: none;
}
@media (max-width: 600px) {
  .task-card {
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
