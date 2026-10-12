<script setup lang="ts">
// 任务的「相关」：转出它的那段讨论里摆出来的文档、文件，点了在面板里打开。它是从哪
// 句话来的在任务页顶上（TaskSourceLine）。直接新建的任务没有这些，整段不画。
import type { TaskRelated } from '@/types/taskOrigin'

import { computed } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{
  related: TaskRelated | null
  memberNames: Record<string, string>
}>()

const emit = defineEmits<{
  (e: 'open-document', id: string, title: string): void
  (e: 'open-file', path: string): void
}>()

const materials = computed(() => props.related?.materials ?? [])

function nameOf(handle: string): string {
  return props.memberNames[handle] || handle
}
function fileName(path: string): string {
  return path.split('/').pop() || path
}
</script>

<template>
  <section v-if="materials.length" class="related" data-testid="task-related">
    <h3 class="related__title">{{ t('work.task.related.title') }}</h3>
    <ul class="related__materials">
      <li v-for="item in materials" :key="item.kind === 'document' ? `d:${item.id}` : `f:${item.path}`">
        <button
          v-if="item.kind === 'document'"
          type="button"
          class="related__item"
          @click="emit('open-document', item.id, item.title)"
        >
          <v-icon size="16" class="related__icon">mdi-file-document-outline</v-icon>
          <span class="t-body related__name">{{ item.title || t('work.task.related.untitled') }}</span>
          <span class="t-meta c-faint">{{ t('work.task.related.by', { name: nameOf(item.by) }) }}</span>
        </button>
        <button v-else type="button" class="related__item" @click="emit('open-file', item.path)">
          <v-icon size="16" class="related__icon">mdi-paperclip</v-icon>
          <span class="t-body related__name">{{ fileName(item.path) }}</span>
          <span class="t-meta c-faint">{{ t('work.task.related.by', { name: nameOf(item.by) }) }}</span>
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.related {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  border-top: 1px solid var(--line);
}
.related__title {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 600;
  color: var(--ink);
}
.related__item:hover {
  background: var(--fill);
}
.related__materials {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.related__item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 4px 6px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.related__icon {
  color: var(--muted);
}
.related__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}
</style>
