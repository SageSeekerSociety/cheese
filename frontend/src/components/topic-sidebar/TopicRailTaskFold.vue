<script setup lang="ts">
// 频道名前的任务折叠箭头；收起后的状态由频道行统一合并成一盏灯。
import { computed } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{
  collapsed: boolean
  page?: boolean
}>()
const emit = defineEmits<{ (e: 'toggle'): void }>()
const title = computed(() => t(props.collapsed ? 'work.sidebar.expandTasks' : 'work.sidebar.collapseTasks'))
</script>

<template>
  <button
    type="button"
    class="task-fold__btn"
    :class="{ 'task-fold__btn--collapsed': collapsed, 'task-fold__btn--page': page }"
    :title="title"
    :aria-label="title"
    :aria-expanded="!collapsed"
    @click.stop="emit('toggle')"
  >
    <v-icon size="15">{{ collapsed ? 'mdi-chevron-right' : 'mdi-chevron-down' }}</v-icon>
  </button>
</template>

<style scoped>
.task-fold__btn {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.task-fold__btn :deep(.v-icon) {
  color: var(--faint);
}
.task-fold__btn:hover :deep(.v-icon) {
  color: var(--text);
}
.task-fold__btn--collapsed :deep(.v-icon) {
  color: var(--muted);
}
</style>
