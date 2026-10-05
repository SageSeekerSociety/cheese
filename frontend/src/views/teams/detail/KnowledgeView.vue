<script setup lang="ts">
// 小队知识库（`/teams/:handle/knowledge`）**画的那一半**。
//
// 取数与写入、判断，都在 `composables/useTeamKnowledge.ts`（容器 `Knowledge.vue` 调它）。
// 这里只吃 props：一律发「变的是什么」往上，重不重取一页由容器决定，所以工具栏里一行
// 取数都没有。上传对话框交上来的草稿也只往上递，成功关门是容器的事。
import type { KnowledgeDraft } from '@/lib/knowledgeDraft'
import type { Knowledge, KnowledgeContentData, KnowledgeType } from '@/types'

import BaseLoadError from '@/components/base/BaseLoadError.vue'
import KnowledgeDetailDialog from '@/components/teams/knowledge/KnowledgeDetailDialog.vue'
import KnowledgeEmpty from '@/components/teams/knowledge/KnowledgeEmpty.vue'
import KnowledgeGrid from '@/components/teams/knowledge/KnowledgeGrid.vue'
import KnowledgeTable from '@/components/teams/knowledge/KnowledgeTable.vue'
import KnowledgeToolbar from '@/components/teams/knowledge/KnowledgeToolbar.vue'
import KnowledgeUploadDialog from '@/components/teams/knowledge/KnowledgeUploadDialog.vue'
import { t } from '@/i18n'
import { RESOURCE_TYPE_OPTIONS } from '@/lib/knowledgeFormat'

defineProps<{
  loading: boolean
  searchQuery: string | null
  typeFilter: KnowledgeType | null
  tagFilter: string | null
  viewMode: string
  knowledges: Knowledge[]
  /** 这一页没读出来（真的读到了、里面是空的，是另一回事，由 `hasFilters` 的空态说）。 */
  failed: boolean
  /** 没读到时的说明行。 */
  reason: string | null
  /** 401/403：重试没有意义，不给重试。 */
  forbidden: boolean
  hasFilters: boolean
  availableTags: string[]
  ownerId: string | null
  detailDialog: boolean
  selectedResource: Knowledge | null
  selectedResourceContent: KnowledgeContentData
  uploadDialog: boolean
  uploading: boolean
}>()

defineEmits<{
  'update:searchQuery': [value: string | null]
  'update:typeFilter': [value: KnowledgeType | null]
  'update:tagFilter': [value: string | null]
  'update:viewMode': [value: string]
  'update:detailDialog': [value: boolean]
  'update:uploadDialog': [value: boolean]
  change: []
  upload: []
  retry: []
  open: [resource: Knowledge]
  openLink: [resource: Knowledge]
  delete: [resource: Knowledge]
  submit: [draft: KnowledgeDraft]
}>()
</script>

<template>
  <v-container class="px-6 py-4" fluid>
    <div v-if="loading" class="loading-container py-8">
      <v-progress-circular indeterminate color="primary" class="mx-auto d-block"></v-progress-circular>
    </div>

    <div v-else>
      <KnowledgeToolbar
        :search-query="searchQuery"
        :type-filter="typeFilter"
        :tag-filter="tagFilter"
        :view-mode="viewMode"
        :resource-types="RESOURCE_TYPE_OPTIONS"
        :available-tags="availableTags"
        @update:search-query="$emit('update:searchQuery', $event)"
        @update:type-filter="$emit('update:typeFilter', $event)"
        @update:tag-filter="$emit('update:tagFilter', $event)"
        @update:view-mode="$emit('update:viewMode', $event)"
        @change="$emit('change')"
        @upload="$emit('upload')"
      />

      <!-- 没读出来就说没读出来，「这个团队还没有资料」是另一回事，由下面的空态说。 -->
      <BaseLoadError
        v-if="failed"
        :title="t('teams.knowledge.loadFailed')"
        :error="reason"
        :forbidden="forbidden"
        @retry="$emit('retry')"
      />

      <KnowledgeEmpty v-else-if="knowledges.length === 0" :has-filters="hasFilters" />

      <KnowledgeGrid
        v-else-if="viewMode === 'grid'"
        :items="knowledges"
        @open="$emit('open', $event)"
        @open-link="$emit('openLink', $event)"
      />

      <KnowledgeTable
        v-else
        :items="knowledges"
        :owner-id="ownerId"
        @open="$emit('open', $event)"
        @open-link="$emit('openLink', $event)"
        @delete="$emit('delete', $event)"
      />
    </div>

    <KnowledgeDetailDialog
      :model-value="detailDialog"
      :resource="selectedResource"
      :content="selectedResourceContent"
      :owner-id="ownerId"
      @update:model-value="$emit('update:detailDialog', $event)"
      @open-link="$emit('openLink', $event)"
      @delete="$emit('delete', $event)"
    />

    <KnowledgeUploadDialog
      :model-value="uploadDialog"
      :uploading="uploading"
      :available-tags="availableTags"
      @update:model-value="$emit('update:uploadDialog', $event)"
      @submit="$emit('submit', $event)"
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
</style>
