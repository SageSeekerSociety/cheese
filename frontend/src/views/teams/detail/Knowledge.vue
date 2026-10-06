<script setup lang="ts">
import { computed } from 'vue'

import { useTeamKnowledge } from '@/composables/useTeamKnowledge'

import BaseLoadError from '@/components/base/BaseLoadError.vue'
import KnowledgeDetailDialog from '@/components/teams/knowledge/KnowledgeDetailDialog.vue'
import KnowledgeEmpty from '@/components/teams/knowledge/KnowledgeEmpty.vue'
import KnowledgeGrid from '@/components/teams/knowledge/KnowledgeGrid.vue'
import KnowledgeTable from '@/components/teams/knowledge/KnowledgeTable.vue'
import KnowledgeToolbar from '@/components/teams/knowledge/KnowledgeToolbar.vue'
import KnowledgeUploadDialog from '@/components/teams/knowledge/KnowledgeUploadDialog.vue'
import { t } from '@/i18n'
import { RESOURCE_TYPE_OPTIONS } from '@/lib/knowledgeFormat'
import { isForbidden, loadFailureReason } from '@/lib/loadFailure'

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

/** 上传对话框递上来的那份草稿：成功才关门。 */
async function onSubmitUpload(draft: Parameters<typeof createKnowledge>[0]) {
  if (await createKnowledge(draft)) {
    uploadDialog.value = false
  }
}
</script>

<template>
  <v-container class="px-6 py-4" fluid>
    <div v-if="loading" class="loading-container py-8">
      <v-progress-circular indeterminate color="primary" class="mx-auto d-block"></v-progress-circular>
    </div>

    <div v-else>
      <KnowledgeToolbar
        :search-query="searchQuery"
        :type-filter="filter.type"
        :tag-filter="filter.tag"
        :view-mode="viewMode"
        :resource-types="RESOURCE_TYPE_OPTIONS"
        :available-tags="availableTags"
        @update:search-query="searchQuery = $event"
        @update:type-filter="filter.type = $event"
        @update:tag-filter="filter.tag = $event"
        @update:view-mode="viewMode = $event"
        @change="loadKnowledges"
        @upload="openUploadDialog"
      />

      <!-- 没读出来就说没读出来，「这个团队还没有资料」是另一回事，由下面的空态说。 -->
      <BaseLoadError
        v-if="failed"
        :title="t('teams.knowledge.loadFailed')"
        :error="loadFailureReason(loadError)"
        :forbidden="isForbidden(loadError)"
        @retry="loadKnowledges"
      />

      <KnowledgeEmpty v-else-if="knowledges.length === 0" :has-filters="hasFilters" />

      <KnowledgeGrid
        v-else-if="viewMode === 'grid'"
        :items="knowledges"
        @open="openResourceDetail"
        @open-link="openResourceLink"
      />

      <KnowledgeTable
        v-else
        :items="knowledges"
        :owner-id="ownerId"
        @open="openResourceDetail"
        @open-link="openResourceLink"
        @delete="confirmDeleteResource"
      />
    </div>

    <KnowledgeDetailDialog
      v-model="resourceDetailDialog"
      :resource="selectedResource"
      :content="selectedResourceContent"
      :owner-id="ownerId"
      @open-link="openResourceLink"
      @delete="confirmDeleteResource"
    />

    <KnowledgeUploadDialog
      v-model="uploadDialog"
      :uploading="uploading"
      :available-tags="availableTags"
      @submit="onSubmitUpload"
    />
  </v-container>
</template>

<style scoped lang="scss">
.loading-container {
  min-height: 300px;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* 这一页只留页面级的布局样式。资料块和代码块的观感跟着各自的元素走，
   写在拥有那个节点的组件里：
     - `.resource-preview`（含四支分类色）→ `components/teams/knowledge/KnowledgeGrid.vue`
     - `.code-block`（含深色分支）→ `components/teams/knowledge/KnowledgeDetailDialog.vue`
   原因不是洁癖：详情对话框走 Teleport，节点最终挂在 `body` 下，
   `[data-v-这一页] .xxx` 这种带祖先条件的写法就再也够不着它了
   （`frontend/src/views/teams/detail/Knowledge.spec.ts` 里有一条回归测试盯着
   「传给对话框的样式不许再依赖页面作用域」）。颜色字面量收在
   `src/style.css` 的 `--category-*` / `--code-bg` / `--code-ink` 里。
   --------------------------------------------------------------------------- */
</style>
