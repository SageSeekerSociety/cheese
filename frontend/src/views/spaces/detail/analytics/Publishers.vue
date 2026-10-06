<template>
  <PublishersView
    v-model:sort-by="sortByModel"
    v-model:sort-order="sortOrderModel"
    :publishers="publishers"
    :loading="loading"
    :failed="failed"
    :error-detail="errorDetail"
    :exporting="exporting"
    @retry="load"
    @export="onExport"
  />
</template>

<script setup lang="ts">
// 出题人这一格的容器：读排序、按筛选拉榜、导出 CSV。画面在 `PublishersView.vue`
// （场景规则见 docs/manual/dev/scenes.md）。
import type { SpaceAnalyticsPublisherMetrics } from '@/network/api/spaces/types'

import { ref, watch } from 'vue'

import { useAnalyticsExport } from './composables/useAnalyticsExport'
import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import PublishersView from './PublishersView.vue'
import { buildAnalyticsApiParams } from './utils'

import { SpacesApi } from '@/network/api/spaces'

const { filters, replaceFilters, spaceId } = useSpaceAnalyticsFilters()

const loading = ref(false)
// 读失败和「还没有出题人」是两件事：失败替换掉表格，空状态才交给表格自己说。
const failed = ref(false)
const errorDetail = ref<string | null>(null)
const publishers = ref<SpaceAnalyticsPublisherMetrics[]>([])
const sortByModel = ref(filters.value.sortBy || 'taskCount')
const sortOrderModel = ref(filters.value.sortOrder || 'desc')

const { exporting, exportCsv } = useAnalyticsExport(spaceId, filters)

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
    const { data } = await SpacesApi.getAnalyticsPublishers(
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

const onExport = () => {
  exportCsv('publishers').catch(() => undefined)
}
</script>
