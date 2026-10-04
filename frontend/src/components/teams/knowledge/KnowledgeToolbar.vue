<script setup lang="ts">
// 知识库页顶上那一行：搜索、两颗筛选、上传、网格 / 列表切换。
//
// 它不取数也不筛：三个筛选各自把新值报上去（`update:*`），再加一个 `change`
// 说「刚才那一下是筛选」，由页决定要不要重取一页。分开报而不是在件里判断，
// 是因为「哪三个算筛选」是这一页的事，不是这一行控件的事。
import type { KnowledgeType } from '@/types'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { knowledgeTypeLabel } from '@/lib/knowledgeFormat'

defineOptions({ name: 'KnowledgeToolbar' })

const props = defineProps<{
  searchQuery: string | null
  typeFilter: KnowledgeType | null
  tagFilter: string | null
  viewMode: string
  resourceTypes: KnowledgeType[]
  availableTags: string[]
}>()

const emit = defineEmits<{
  'update:searchQuery': [value: string | null]
  'update:typeFilter': [value: KnowledgeType | null]
  'update:tagFilter': [value: string | null]
  'update:viewMode': [value: string]
  /** 三个筛选里的任何一个变了值：外面据此重取一页。 */
  change: []
  upload: []
}>()

const resourceTypeItems = computed(() =>
  props.resourceTypes.map((type) => ({ title: knowledgeTypeLabel(type), value: type }))
)

// 清空（`clearable` 那颗叉）会报 null 回来，原样往上递：请求里「没有 query」
// 和「query 是空串」是同一件事，但状态里留着 null 才是这一页一直以来的样子。
function pickSearch(value: unknown) {
  emit('update:searchQuery', (value as string | null) ?? null)
  emit('change')
}

function pickType(value: unknown) {
  emit('update:typeFilter', (value as KnowledgeType | null) ?? null)
  emit('change')
}

function pickTag(value: unknown) {
  emit('update:tagFilter', (value as string | null) ?? null)
  emit('change')
}
</script>

<template>
  <div class="mb-4 d-flex align-center flex-wrap gap-4">
    <v-text-field
      :model-value="searchQuery"
      autocomplete="off"
      :label="t('teams.knowledge.searchLabel')"
      prepend-inner-icon="mdi-magnify"
      density="compact"
      variant="solo"
      rounded="lg"
      hide-details
      class="knowledge-search"
      clearable
      @update:model-value="pickSearch"
    ></v-text-field>

    <v-select
      :model-value="typeFilter"
      autocomplete="off"
      :label="t('teams.knowledge.resourceTypeLabel')"
      density="compact"
      variant="solo"
      flat
      hide-details
      rounded="lg"
      clearable
      :items="resourceTypeItems"
      class="resource-type-filter"
      @update:model-value="pickType"
    ></v-select>

    <v-select
      :model-value="tagFilter"
      autocomplete="off"
      :label="t('teams.knowledge.tagLabel')"
      density="compact"
      variant="solo"
      flat
      hide-details
      rounded="lg"
      clearable
      :items="availableTags"
      class="tag-filter"
      @update:model-value="pickTag"
    ></v-select>

    <v-spacer></v-spacer>

    <BaseButton kind="primary" prepend-icon="mdi-upload" class="mr-2" @click="emit('upload')">{{
      t('teams.knowledge.upload')
    }}</BaseButton>

    <v-btn-toggle
      :model-value="viewMode"
      density="comfortable"
      variant="outlined"
      rounded="lg"
      @update:model-value="emit('update:viewMode', $event)"
    >
      <!-- eslint-disable-next-line vue/no-restricted-syntax -- a segment of v-btn-toggle, not one of the BaseButton roles -->
      <v-btn value="grid" icon="mdi-view-grid"></v-btn>
      <!-- eslint-disable-next-line vue/no-restricted-syntax -- a segment of v-btn-toggle, not one of the BaseButton roles -->
      <v-btn value="list" icon="mdi-view-list"></v-btn>
    </v-btn-toggle>
  </div>
</template>

<style scoped lang="scss">
.knowledge-search {
  min-width: 280px;
  max-width: 500px;
}

.resource-type-filter,
.tag-filter {
  min-width: 140px;
}

// 响应式调整
@media (max-width: 600px) {
  .knowledge-search {
    width: 100%;
  }
}
</style>
