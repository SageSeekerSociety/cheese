<script setup lang="ts">
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

// 小队知识库（`/teams/:handle/knowledge`）的画法：四种视图、两张对话框，只吃数据、往上报
// 动作。取数与写都在页面里（`Knowledge.vue`）—— 所以工具栏三条筛选只报「变的是什么」，
// 重不重取一页由页面那一层决定（`@change`），这里一行取数都没有。
//
// 筛选那三个值**不在这半张画面里改**：`v-model:search-query` / `v-model:view-mode` /
// `v-model:type-filter` / `v-model:tag-filter` 四个具名 model 报上去，页面再落进它那一份
// 状态（`filter.type` / `filter.tag` 就在那儿）。原来这一层是直接 `filter.type = $event`
// 改一个对象，那是在改递上来的 prop，Vue 会当场告警；改成 model 之后改的仍是同一份值，
// 只是经由上报走一趟。
defineOptions({ name: 'KnowledgeView' })

defineProps<{
  loading: boolean
  knowledges: Knowledge[]
  hasFilters: boolean
  availableTags: string[]
  ownerId?: number
  /** 这一页没读出来。空列表只在「真的读到了、里面是空的」时才算数 —— 读失败也留一个
   *  空列表，两种长得一模一样，所以要说一声。 */
  failed: boolean
  /** 读失败时服务端原话（取不到时是 null）；`forbidden` 是 401/403。由页面算好递上来，
   *  这一半不碰 `loadFailure`。 */
  reason: string | null
  forbidden: boolean
  selectedResource: Knowledge | null
  selectedResourceContent: KnowledgeContentData
  uploading: boolean
}>()

const emit = defineEmits<{
  /** 三条筛选里变了任意一条：重不重取一页由页面决定。 */
  change: []
  upload: []
  open: [resource: Knowledge]
  openLink: [resource: Knowledge]
  delete: [resource: Knowledge]
  /** 上传对话框递上来的那份草稿。成不成功、关不关门都由页面那一层说了算。 */
  submit: [draft: KnowledgeDraft]
}>()

const searchQuery = defineModel<string | null>('searchQuery', { required: true })
const viewMode = defineModel<string>('viewMode', { required: true })
const typeFilter = defineModel<KnowledgeType | null>('typeFilter', { required: true })
const tagFilter = defineModel<string | null>('tagFilter', { required: true })
const resourceDetailDialog = defineModel<boolean>('resourceDetailDialog', { required: true })
const uploadDialog = defineModel<boolean>('uploadDialog', { required: true })
</script>

<template>
  <v-container class="px-6 py-4" fluid>
    <div v-if="loading" class="loading-container py-8">
      <v-progress-circular indeterminate color="primary" class="mx-auto d-block"></v-progress-circular>
    </div>

    <div v-else>
      <KnowledgeToolbar
        v-model:search-query="searchQuery"
        v-model:type-filter="typeFilter"
        v-model:tag-filter="tagFilter"
        v-model:view-mode="viewMode"
        :resource-types="RESOURCE_TYPE_OPTIONS"
        :available-tags="availableTags"
        @change="emit('change')"
        @upload="emit('upload')"
      />

      <!-- 没读出来就说没读出来，「这个团队还没有资料」是另一回事，由下面的空态说。 -->
      <BaseLoadError
        v-if="failed"
        :title="t('teams.knowledge.loadFailed')"
        :error="reason"
        :forbidden="forbidden"
        @retry="emit('change')"
      />

      <KnowledgeEmpty v-else-if="knowledges.length === 0" :has-filters="hasFilters" />

      <KnowledgeGrid
        v-else-if="viewMode === 'grid'"
        :items="knowledges"
        @open="emit('open', $event)"
        @open-link="emit('openLink', $event)"
      />

      <KnowledgeTable
        v-else
        :items="knowledges"
        :owner-id="ownerId"
        @open="emit('open', $event)"
        @open-link="emit('openLink', $event)"
        @delete="emit('delete', $event)"
      />
    </div>

    <KnowledgeDetailDialog
      v-model="resourceDetailDialog"
      :resource="selectedResource"
      :content="selectedResourceContent"
      :owner-id="ownerId"
      @open-link="emit('openLink', $event)"
      @delete="emit('delete', $event)"
    />

    <KnowledgeUploadDialog
      v-model="uploadDialog"
      :uploading="uploading"
      :available-tags="availableTags"
      @submit="emit('submit', $event)"
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
