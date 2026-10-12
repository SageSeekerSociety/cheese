<script setup lang="ts">
import { computed } from 'vue'

import { useTeamKnowledge } from '@/composables/useTeamKnowledge'

import KnowledgeView from './KnowledgeView.vue'

import { isForbidden, loadFailureReason } from '@/lib/loadFailure'

// 小队知识库（`/teams/:handle/knowledge`）。这一页原来 1508 行，装了三件事：
// 取数与写入、四种视图的模板、以及和它们搅在一起的一堆判断。现在它只剩**接线** ——
// 判断在 `composables/useTeamKnowledge.ts`，画法在 `KnowledgeView.vue`，两张对应关系
// （哪一件点哪一下、递什么上去）看下面那个模板就够了：
//
//   1. **读**。工具栏三颗筛选只报「变的是什么」，重不重取一页由这一层决定 ——
//      所以这里能看到 `@change="loadKnowledges"` 一条线，而画面那一半一行取数都没有。
//   2. **写**。上传对话框自己拿着草稿、自己校验，交上来一份 dto；这一层把它递给
//      composable，成功了才关掉对话框（失败留着让人改一改再试）。
//
// 工具栏那三条筛选是**具名 model**：画面报上来 `update:type-filter` / `update:tag-filter`，
// 落到下面这层拥有的 `filter` 上。以前画面直接改 `filter.type`，那是在改递上来的 prop。
defineOptions({ name: 'Knowledge' })

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

/** `filter` 里那两格摊成具名 model，画面那两个 v-model 落回同一个对象上。 */
const typeFilter = computed({
  get: () => filter.value.type,
  set: (value) => {
    filter.value.type = value
  },
})
const tagFilter = computed({
  get: () => filter.value.tag,
  set: (value) => {
    filter.value.tag = value
  },
})

/**
 * 这一页没读出来。空列表只在「真的读到了、里面是空的」时才算数 ——
 * 读失败也留一个空列表，两种长得一模一样，所以要看 `loadError`。
 */
const failed = computed(() => loadError.value !== null && knowledges.value.length === 0)

/** 上传对话框递上来的那份草稿：成功才关门。 */
async function onSubmitUpload(draft: Parameters<typeof createKnowledge>[0]) {
  if (await createKnowledge(draft)) {
    uploadDialog.value = false
  }
}
</script>

<template>
  <KnowledgeView
    v-model:view-mode="viewMode"
    v-model:search-query="searchQuery"
    v-model:type-filter="typeFilter"
    v-model:tag-filter="tagFilter"
    v-model:resource-detail-dialog="resourceDetailDialog"
    v-model:upload-dialog="uploadDialog"
    :loading="loading"
    :knowledges="knowledges"
    :has-filters="hasFilters"
    :available-tags="availableTags"
    :owner-id="ownerId"
    :failed="failed"
    :reason="loadFailureReason(loadError)"
    :forbidden="isForbidden(loadError)"
    :selected-resource="selectedResource"
    :selected-resource-content="selectedResourceContent"
    :uploading="uploading"
    @change="loadKnowledges"
    @upload="openUploadDialog"
    @open="openResourceDetail"
    @open-link="openResourceLink"
    @delete="confirmDeleteResource"
    @submit="onSubmitUpload"
  />
</template>
