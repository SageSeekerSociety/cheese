<script setup lang="ts">
// 任务的实况文档：现在和开始时相比改了哪几段。审阅的人据此看「开始后定下的事」。
import { computed } from 'vue'

import { compareTexts } from '../../lib/paragraphDiff'

import { t } from '@/i18n'

const props = defineProps<{
  before: string
  after: string
}>()

const diff = computed(() => compareTexts(props.before, props.after))
</script>

<template>
  <div class="doc-compare">
    <div class="doc-compare__summary t-meta">
      <span>{{ t('tasks.artifact.paragraphsChanged', { n: diff.changed }) }}</span>
      <span>{{ t('tasks.artifact.paragraphsAdded', { n: diff.added }) }}</span>
      <span>{{ t('tasks.artifact.paragraphsRemoved', { n: diff.removed }) }}</span>
    </div>
    <p v-if="!diff.paragraphs.length" class="t-body c-muted">{{ t('work.task.compareSame') }}</p>
    <ol v-else class="doc-compare__paragraphs">
      <li v-for="(paragraph, index) in diff.paragraphs" :key="index" class="doc-compare__paragraph">
        <p v-if="paragraph.kind === 'changed'" class="t-body doc-compare__text">
          <template v-for="(segment, s) in paragraph.segments" :key="s">
            <ins v-if="segment.kind === 'add'">{{ segment.text }}</ins>
            <del v-else-if="segment.kind === 'del'">{{ segment.text }}</del>
            <template v-else>{{ segment.text }}</template>
          </template>
        </p>
        <p v-else-if="paragraph.kind === 'added'" class="t-body doc-compare__text doc-compare__text--added">
          {{ paragraph.text }}
        </p>
        <p v-else class="t-body doc-compare__text doc-compare__text--removed">{{ paragraph.text }}</p>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.doc-compare {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
}
.doc-compare__summary {
  display: flex;
  gap: 12px;
  color: var(--muted);
}
.doc-compare__paragraphs {
  margin: 0;
  padding: 0;
  list-style: none;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}
.doc-compare__paragraph {
  padding: 12px 16px;
}
.doc-compare__paragraph + .doc-compare__paragraph {
  border-top: 1px solid var(--line);
}
.doc-compare__text {
  margin: 0;
  color: var(--text);
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.doc-compare__text ins {
  background: var(--ok-wash);
  color: var(--ok-ink);
  text-decoration: none;
}
.doc-compare__text del {
  background: var(--danger-wash);
  color: var(--danger-ink);
}
.doc-compare__text--added {
  padding: 8px 12px;
  border-radius: var(--radius-md);
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.doc-compare__text--removed {
  padding: 8px 12px;
  border-radius: var(--radius-md);
  background: var(--danger-wash);
  color: var(--danger-ink);
  text-decoration: line-through;
}
</style>
