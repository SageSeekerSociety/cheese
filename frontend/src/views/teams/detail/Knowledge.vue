<script setup lang="ts">
import { useTeamKnowledge } from '@/composables/useTeamKnowledge'

import KnowledgeDetailDialog from '@/components/teams/knowledge/KnowledgeDetailDialog.vue'
import KnowledgeEmpty from '@/components/teams/knowledge/KnowledgeEmpty.vue'
import KnowledgeGrid from '@/components/teams/knowledge/KnowledgeGrid.vue'
import KnowledgeTable from '@/components/teams/knowledge/KnowledgeTable.vue'
import KnowledgeToolbar from '@/components/teams/knowledge/KnowledgeToolbar.vue'
import KnowledgeUploadDialog from '@/components/teams/knowledge/KnowledgeUploadDialog.vue'
import { RESOURCE_TYPE_OPTIONS } from '@/lib/knowledgeFormat'

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

      <KnowledgeEmpty v-if="knowledges.length === 0" :has-filters="hasFilters" />

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
      @update:content="selectedResourceContent = $event"
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

/* ---------------------------------------------------------------------------
   下面这两块是这一页**故意留着**的样式，不跟着各自的组件走。

   `stylelint-baseline.json` 是**按文件**记的（`scripts/stylelint-ratchet.mjs`
   只拦「新文件从 0 涨起来」，一涨就红），而这两块恰好都在基线里：`.resource-preview`
   那四支分类色各自算一条硬编码色，`.code-block` 那对深浅色算两条。跟着组件搬进
   新建的 `.vue` 里，等于让一个新文件从 0 涨到 4 / 2 —— 门会挡下来，而这两处颜色
   该不该 token 化是设计系统那一层的事（见下面两段注释和
   docs/design-system.md），不是这次拆分该顺手决定的。

   所以它们留在这里、跨一层写给子组件里的元素：`[data-v-这一页] .resource-preview`
   照样命中，因为那两个元素都在这一页的子树里。
   --------------------------------------------------------------------------- */

/* 资料类型那四支底色是【分类色】不是状态色：蓝=资料 / 绿=文本 / 橙=链接 / 青=代码，
   色相本身就是这条信息，所以两个主题下必须是同一个色相，不能换成会翻转的
   token。它们是 8% 的淡色叠加（不是实心填充），压在深色卡片上仍然成立，只是
   更淡。设计系统目前没有"分类色"这一档 —— 要不要新增是 token 层的决定，
   属于父话题，这里先原样保留（已在存量基线里）。 */
:deep(.resource-preview) {
  height: 140px;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: var(--fill);

  &.resource-type-material {
    background-color: rgba(63, 81, 181, 0.08);
  }

  &.resource-type-text {
    background-color: rgba(76, 175, 80, 0.08);
  }

  &.resource-type-link {
    background-color: rgba(255, 152, 0, 0.08);
  }

  &.resource-type-code {
    background-color: rgba(3, 169, 244, 0.08);
  }
}

/* 代码块是【故意反色】的元素：浅色主题下它是深底浅字，这是设计意图，不是漏掉的
   token 化。但深色主题下不能照抄 —— #212121 压在 --surface(#1B1D20) 上只有
   1.1:1，整块会糊进卡片里。所以深色分支往上提一档，用比 surface 更亮的
   --fill-2(#282C31)，文字用 --text(#D3D6DB)，9.6:1。
   做法照抄波次 1.5 在 RailItem 悬浮提示上的处理：组件内局部变量 + 一个真正站得住
   的 [data-theme='dark'] 分支（这个元素要反转两次，不是选错了 token）。
   浅色的两个值只能写死：设计系统里没有"在浅色主题下也是深色"的 token。 */
:deep(.code-block) {
  --code-bg: #212121;
  --code-ink: #e0e0e0; /* 13.8:1 on #212121 */

  overflow-x: auto;
  color: var(--code-ink);
  background-color: var(--code-bg);
  font-family: 'Fira Code', monospace;
  font-size: 0.9rem;
  line-height: 1.5;

  pre {
    margin: 0;
  }
}

:root[data-theme='dark'] :deep(.code-block) {
  --code-bg: var(--fill-2);
  --code-ink: var(--text);
}
</style>
