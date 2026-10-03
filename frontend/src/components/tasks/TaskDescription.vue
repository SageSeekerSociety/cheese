<template>
  <!-- 题目详情的正文：TipTap JSON 或 Markdown，两种都认。题目页和审核队列用同一份，
       审核的人看到的就是领题的人将来看到的。 -->
  <TipTapViewer v-if="tipTap" class="td" :value="tipTap" data-user-content />
  <div v-else-if="markdown" class="markdown-body td t-reading" data-user-content v-html="markdown" />
  <p v-else class="td__empty">{{ empty }}</p>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent } from 'vue'

import { MarkdownRenderer } from '@/components/chat/services/markdownRenderer'

const TipTapViewer = defineAsyncComponent(() => import('@/components/common/Editor/TipTapViewer.vue'))
const markdownRenderer = new MarkdownRenderer()

const props = defineProps<{
  source: string | null | undefined
  /** 没写详情时显示的那句话。 */
  empty: string
}>()

const tipTap = computed(() => {
  const raw = props.source ?? ''
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw)
    return typeof parsed === 'object' && parsed !== null && parsed.type === 'doc' ? parsed : null
  } catch {
    return null
  }
})

const markdown = computed(() => {
  const raw = props.source ?? ''
  if (!raw || tipTap.value) return ''
  return markdownRenderer.render(raw)
})
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
  color: var(--muted);
  font-size: 13px;
}
</style>
