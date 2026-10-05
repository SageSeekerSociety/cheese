<script setup lang="ts">
// 小队知识库（`/teams/:handle/knowledge`）。这一页原来 1508 行，装了三件事：
// 取数与写入、四种视图的模板、以及和它们搅在一起的一堆判断。现在它只剩**接线**
// —— 判断在 `composables/useTeamKnowledge.ts`，画法在
// `components/teams/knowledge/*.vue`，两张对应关系（哪一件点哪一下、递什么上去）
// 看下面那个模板就够了：
//
//   1. **读**。工具栏三颗筛选只报「变的是什么」，重不重取一页由这一层决定 ——
//      所以这里能看到 `@change="loadKnowledges"` 一条线，而工具栏里一行取数都没有。
//   2. **写**。上传对话框自己拿着草稿、自己校验，交上来一份 dto；这一层把它递给
//      composable，成功了才关掉对话框（失败留着让人改一改再试）。
//
// 容器把状态与去处接上，画的那一半在 `KnowledgeView.vue`（只吃 props）。
import type { KnowledgeDraft } from '@/lib/knowledgeDraft'

import { computed } from 'vue'

import { useTeamKnowledge } from '@/composables/useTeamKnowledge'

import KnowledgeView from './KnowledgeView.vue'

import { isForbidden, loadFailureReason } from '@/lib/loadFailure'

defineOptions({ name: 'KnowledgeView' })

const {
  loading,
  viewMode,
  searchQuery,
  filter,
  knowledges,
  loadError,
  hasFilters,
  availableTags,
  ownerId,
  loadKnowledges,
  resourceDetailDialog,
  selectedResource,
  selectedResourceContent,
  openResourceDetail,
  openResourceLink,
  confirmDeleteResource,
  uploadDialog,
  uploading,
  openUploadDialog,
  createKnowledge,
} = useTeamKnowledge()

/**
 * 这一页没读出来。空列表只在「真的读到了、里面是空的」时才算数 ——
 * 读失败也留一个空列表，两种长得一模一样，所以要看 `loadError`。
 */
const failed = computed(() => loadError.value !== null && knowledges.value.length === 0)
const failureReason = computed(() => loadFailureReason(loadError.value))
const forbidden = computed(() => isForbidden(loadError.value))

/** 上传对话框递上来的那份草稿：成功才关门。 */
async function onSubmitUpload(draft: KnowledgeDraft) {
  if (await createKnowledge(draft)) {
    uploadDialog.value = false
  }
}
</script>

<template>
  <KnowledgeView
    :loading="loading"
    :search-query="searchQuery"
    :type-filter="filter.type"
    :tag-filter="filter.tag"
    :view-mode="viewMode"
    :knowledges="knowledges"
    :failed="failed"
    :reason="failureReason"
    :forbidden="forbidden"
    :has-filters="hasFilters"
    :available-tags="availableTags"
    :owner-id="ownerId"
    :detail-dialog="resourceDetailDialog"
    :selected-resource="selectedResource"
    :selected-resource-content="selectedResourceContent"
    :upload-dialog="uploadDialog"
    :uploading="uploading"
    @update:search-query="searchQuery = $event"
    @update:type-filter="filter.type = $event"
    @update:tag-filter="filter.tag = $event"
    @update:view-mode="viewMode = $event"
    @update:detail-dialog="resourceDetailDialog = $event"
    @update:upload-dialog="uploadDialog = $event"
    @change="loadKnowledges"
    @upload="openUploadDialog"
    @retry="loadKnowledges"
    @open="openResourceDetail"
    @open-link="openResourceLink"
    @delete="confirmDeleteResource"
    @submit="onSubmitUpload"
  />
</template>
