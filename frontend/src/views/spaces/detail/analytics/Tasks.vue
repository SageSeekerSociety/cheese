<template>
  <TasksView
    v-model:publisher-id="publisherIdModel"
    v-model:sort-by="sortByModel"
    v-model:sort-order="sortOrderModel"
    v-model:has-pending-approval="hasPendingApprovalModel"
    v-model:has-pending-review="hasPendingReviewModel"
    :tasks="tasks"
    :loading="loading"
    :failed="failed"
    :error-detail="errorDetail"
    :exporting="exporting"
    :publisher-items="publisherItems"
    :publishers-loading="publishersLoading"
    @retry="load"
    @export="onExport"
    @toggle-pending-approval="togglePendingApproval"
    @toggle-pending-review="togglePendingReview"
  />
</template>

<script setup lang="ts">
// 题目这一格的容器：读地址筛选、拉表、按筛选拉出题人、两个待办开关写回地址、导出。
// 画面在 `TasksView.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type { SpaceAnalyticsTask } from '@/network/api/spaces/types'

import { ref, watch } from 'vue'

import { useAnalyticsExport } from './composables/useAnalyticsExport'
import { useAnalyticsPublishers } from './composables/useAnalyticsPublishers'
import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import TasksView from './TasksView.vue'
import { buildAnalyticsApiParams } from './utils'

import { SpacesApi } from '@/network/api/spaces'

const { filters, replaceFilters, spaceId } = useSpaceAnalyticsFilters()

const loading = ref(false)
// 读失败和「还没有题目」是两件事：失败替换掉表格，空状态才交给表格自己说。
const failed = ref(false)
const errorDetail = ref<string | null>(null)
const tasks = ref<SpaceAnalyticsTask[]>([])
const publisherIdModel = ref<number | null>(filters.value.publisherId ?? null)
const sortByModel = ref(filters.value.sortBy || 'createdAt')
const sortOrderModel = ref(filters.value.sortOrder || 'desc')
const hasPendingApprovalModel = ref(Boolean(filters.value.hasPendingApproval))
const hasPendingReviewModel = ref(Boolean(filters.value.hasPendingReview))

const { items: publisherItems, loading: publishersLoading } = useAnalyticsPublishers(spaceId, filters)
const { exporting, exportCsv } = useAnalyticsExport(spaceId, filters)

watch(filters, (value) => {
  publisherIdModel.value = value.publisherId ?? null
  sortByModel.value = value.sortBy || 'createdAt'
  sortOrderModel.value = value.sortOrder || 'desc'
  hasPendingApprovalModel.value = Boolean(value.hasPendingApproval)
  hasPendingReviewModel.value = Boolean(value.hasPendingReview)
})

watch([publisherIdModel, sortByModel, sortOrderModel], async () => {
  await replaceFilters({
    publisherId: publisherIdModel.value ?? undefined,
    sortBy: sortByModel.value,
    sortOrder: sortOrderModel.value,
  })
})

const togglePendingApproval = async () => {
  hasPendingApprovalModel.value = !hasPendingApprovalModel.value
  await replaceFilters({
    hasPendingApproval: hasPendingApprovalModel.value || undefined,
  })
}

const togglePendingReview = async () => {
  hasPendingReviewModel.value = !hasPendingReviewModel.value
  await replaceFilters({
    hasPendingReview: hasPendingReviewModel.value || undefined,
  })
}

const load = async () => {
  loading.value = true
  failed.value = false
  errorDetail.value = null
  try {
    const { data } = await SpacesApi.getAnalyticsTasks(spaceId.value, buildAnalyticsApiParams('tasks', filters.value))
    tasks.value = data.tasks
  } catch (error) {
    console.error('load analytics tasks failed', error)
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
  exportCsv('tasks').catch(() => undefined)
}
</script>
