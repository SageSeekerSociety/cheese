<script setup lang="ts">
// 频道主线上芝士开的一件任务：挂在它那条回复下面的一行，任务名、谁负责、一个箭头，点它去
// 任务页。对话只记发生过的事：这一行不写状态，之后也不在原处变；任务此刻到哪一步看侧栏
// 那一行和任务页。
import { t } from '@/i18n'

defineProps<{
  taskId: string
  title: string
  /** 负责人的名字（按房间里的叫法）；没有负责人时不写。 */
  ownerName: string | null
}>()

const emit = defineEmits<{ (e: 'open', taskId: string): void }>()
</script>

<template>
  <button type="button" class="task-link" data-testid="task-link" :data-task-id="taskId" @click="emit('open', taskId)">
    <v-icon size="16" class="task-link__icon" aria-hidden="true">mdi-checkbox-marked-outline</v-icon>
    <span class="task-link__title">{{ title }}</span>
    <span v-if="ownerName" class="task-link__owner">{{ t('work.room.taskCard.owner', { name: ownerName }) }}</span>
    <v-icon size="16" class="task-link__go" aria-hidden="true">mdi-chevron-right</v-icon>
  </button>
</template>

<style scoped>
.task-link {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  max-width: 620px;
  min-width: 0;
  padding: 8px 12px;
  border: 0;
  border-radius: var(--radius-md);
  background: var(--fill);
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.task-link:hover {
  background: var(--line-2);
}
.task-link__icon,
.task-link__go {
  flex: none;
  color: var(--muted);
}
.task-link__title {
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-link__owner {
  flex: none;
  margin-left: auto;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
}
</style>
