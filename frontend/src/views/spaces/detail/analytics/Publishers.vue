<template>
  <PublishersView
    v-model:sort-by="sortByModel"
    v-model:sort-order="sortOrderModel"
    :loading="loading"
    :failed="failed"
    :error-detail="errorDetail"
    :publishers="publishers"
    :exporting="exporting"
    @load="load"
    @export="exportSection"
  />
</template>

<script setup lang="ts">
import type { SpaceAnalyticsPublisherMetrics } from '@/types/apiSpaces'
import type { AnalyticsSortOrder } from './utils'

import { ref, watch } from 'vue'

import { useAnalyticsExport } from './composables/useAnalyticsExport'
import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import PublishersView from './PublishersView.vue'
import { buildAnalyticsApiParams } from './utils'

import { SpacesApi } from '@/api/spaces'

// 管理员看板「出题人」那一格（`/spaces/:id/analytics/publishers`）：取数与导出在这里，
// 画法在 `PublishersView.vue`。这一层只做三件事 —— 拿 spaceId 取一次、筛选换了重取一次、
// 两个排序下拉写回筛选。判「失败/空/有数据」在画面那一半。
const { filters, replaceFilters, spaceId } = useSpaceAnalyticsFilters()
const { exporting, run: exportSection } = useAnalyticsExport('publishers', spaceId, filters)

const loading = ref(false)
// 读失败和「还没有出题人」是两件事：失败替换掉表格，空状态才交给表格自己说。
const failed = ref(false)
const errorDetail = ref<string | null>(null)
const publishers = ref<SpaceAnalyticsPublisherMetrics[]>([])
const sortByModel = ref(filters.value.sortBy || 'taskCount')
const sortOrderModel = ref<AnalyticsSortOrder>(filters.value.sortOrder || 'desc')

watch(filters, (value) => {
  sortByModel.value = value.sortBy || 'taskCount'
  sortOrderModel.value = value.sortOrder || 'desc'
})

watch([sortByModel, sortOrderModel], async () => {
  await replaceFilters({
    sortBy: sortByModel.value,
    sortOrder: sortOrderModel.value,
  })
})

const load = async () => {
  loading.value = true
  failed.value = false
  errorDetail.value = null
  try {
    const data = await SpacesApi.getAnalyticsPublishers(
      spaceId.value,
      buildAnalyticsApiParams('publishers', filters.value)
    )
    publishers.value = data.publishers
  } catch (error) {
    console.error('load analytics publishers failed', error)
    failed.value = true
    errorDetail.value = error instanceof Error && error.message ? error.message : null
  } finally {
    loading.value = false
  }
}

watch(
  filters,
  () => {
    load().catch(() => undefined)
  },
  { immediate: true }
)
</script>
