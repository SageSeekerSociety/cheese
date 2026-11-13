<script setup lang="ts">
// 任务交了一版去审阅：挂在频道里那一行下面，交的是什么、第几版，点开在频道右边审阅。
// 它记的是交上去的那一刻，不写审阅到哪一步了：退回、采纳在频道里各有自己的一行。
import { t } from '@/i18n'

defineProps<{
  taskId: string
  /** 交的是什么：改动的标题，或文件名。 */
  subject: string | null
  version: number
}>()

const emit = defineEmits<{ (e: 'open', taskId: string): void }>()
</script>

<template>
  <button
    type="button"
    class="delivery-card"
    data-testid="delivery-card"
    :data-task-id="taskId"
    @click="emit('open', taskId)"
  >
    <v-icon size="16" class="delivery-card__icon" aria-hidden="true">mdi-source-branch</v-icon>
    <span v-if="subject" class="delivery-card__subject">{{ subject }}</span>
    <span class="delivery-card__version">{{ t('work.room.delivery.version', { n: version }) }}</span>
    <v-icon size="16" class="delivery-card__go" aria-hidden="true">mdi-chevron-right</v-icon>
  </button>
</template>

<style scoped>
.delivery-card {
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
.delivery-card:hover {
  background: var(--fill);
}
.delivery-card__icon,
.delivery-card__go {
  flex: none;
  color: var(--muted);
}
.delivery-card__subject {
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.delivery-card__version {
  flex: none;
  margin-left: auto;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
}
</style>
