<script setup lang="ts">
// 一份步骤清单（`todo_write`）的那几行：房间总览里的「进度」和一张卡上的「进度」都画它。
// 四周留多少白归放它的那一格，这里只管一行长什么样。
import type { TodoItem } from '../../cx_types'

defineProps<{ items: TodoItem[] }>()

// 三态用填充程度递进，一眼可分：空心圈 = 还没做，半填充 = 做到这里，带勾 = 做完了。
function icon(status: TodoItem['status']): string {
  if (status === 'completed') return 'mdi-check-circle'
  if (status === 'in_progress') return 'mdi-circle-slice-4'
  return 'mdi-circle-outline'
}
</script>

<template>
  <ul class="todo-checklist">
    <li v-for="item in items" :key="item.id" class="progress-item" :class="`progress-item--${item.status}`">
      <v-icon class="progress-item__mark" size="14">{{ icon(item.status) }}</v-icon>
      <span>{{ item.subject }}</span>
    </li>
  </ul>
</template>

<style scoped>
.todo-checklist {
  margin: 0;
  padding: 0;
  list-style: none;
}
.progress-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 2px 0;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}
/* 图标盒子没有文字基线，整行顶对齐，再把图标压到第一行文字的中线上。 */
.progress-item__mark {
  flex: none;
  margin-top: 2px;
  color: var(--faint);
}
/* 做到这一项：字加深加粗，不用琥珀——这里不是主操作，也不是导航位置。 */
.progress-item--in_progress {
  color: var(--ink);
  font-weight: 600;
}
.progress-item--in_progress .progress-item__mark {
  color: var(--muted);
}
.progress-item--completed {
  color: var(--faint);
}
</style>
