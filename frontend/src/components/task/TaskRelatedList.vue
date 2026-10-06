<script setup lang="ts">
// 任务的「相关」：转出它的那段讨论，和讨论里摆出来的文档、文件。点讨论回到那条支线
// 或频道主线，点文档、文件在面板里打开。直接新建的任务没有这些，整段不画。
import type { TaskRelated } from '@/types/taskOrigin'

import { computed } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{
  related: TaskRelated | null
  memberNames: Record<string, string>
}>()

const emit = defineEmits<{
  (e: 'open-discussion', conversationId: string): void
  (e: 'open-document', id: string, title: string): void
  (e: 'open-file', path: string): void
}>()

const origin = computed(() => props.related?.origin ?? null)
const materials = computed(() => props.related?.materials ?? [])

function nameOf(handle: string): string {
  return props.memberNames[handle] || handle
}
function fileName(path: string): string {
  return path.split('/').pop() || path
}
</script>

<template>
  <section v-if="origin || materials.length" class="related" data-testid="task-related">
    <h3 class="related__title">{{ t('work.task.related.title') }}</h3>
    <button
      v-if="origin"
      type="button"
      class="related__origin"
      @click="emit('open-discussion', origin.conversation_id)"
    >
      <span class="t-meta c-faint">{{
        origin.reply_count
          ? t('work.task.related.fromThread', { count: origin.reply_count })
          : t('work.task.related.fromChannel')
      }}</span>
      <span v-if="origin.root" class="t-body related__quote">
        {{ t('work.task.related.quote', { name: nameOf(origin.root.author), text: origin.root.content }) }}
      </span>
    </button>
    <ul v-if="materials.length" class="related__materials">
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
.related__origin {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: none;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.related__origin:hover,
.related__item:hover {
  background: var(--fill);
}
.related__quote {
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  color: var(--text);
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
