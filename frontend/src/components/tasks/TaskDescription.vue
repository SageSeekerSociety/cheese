<template>
  <!-- 题目详情的正文：编辑器的 JSON 文档，或者从 PDF 导入时存下的 Markdown，两种都用
       编辑器那一套画。题目页和审核队列用同一份，审核的人看到的就是领题的人将来看到的。 -->
  <TipTapViewer v-if="doc" class="td" :value="doc" data-user-content />
  <TipTapViewer v-else-if="markdown" class="td" :value="markdown" format="markdown" data-user-content />
  <BaseEmptyState v-else size="inline" class="td__empty" :title="empty" />
</template>

<script setup lang="ts">
import type { JSONContent } from '@tiptap/core'

import { computed, defineAsyncComponent } from 'vue'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'

const TipTapViewer = defineAsyncComponent(() => import('@/components/common/Editor/TipTapViewer.vue'))

const props = defineProps<{
  source: string | null | undefined
  /** 没写详情时显示的那句话。 */
  empty: string
}>()

const doc = computed<JSONContent | null>(() => {
  try {
    const parsed = JSON.parse(props.source ?? '')
    return typeof parsed === 'object' && parsed !== null && parsed.type === 'doc' ? parsed : null
  } catch {
    return null
  }
})

/** 不是编辑器文档的那一份就是 Markdown。 */
const markdown = computed(() => (doc.value || !props.source?.trim() ? '' : props.source))
</script>

<style scoped>
.td :deep(h1),
.td :deep(h2),
.td :deep(h3),
.td :deep(h4) {
  margin: 16px 0 6px;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.td :deep(p) {
  margin: 0 0 8px;
}

.td :deep(ul),
.td :deep(ol) {
  margin: 0 0 8px;
  padding-left: 20px;
}

.td__empty {
  margin: 0 0 6px;
}
</style>
