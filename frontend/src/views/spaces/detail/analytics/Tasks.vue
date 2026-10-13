<template>
  <TasksView
    v-model:publisher-id="publisherIdModel"
    v-model:sort-by="sortByModel"
    v-model:sort-order="sortOrderModel"
    v-model:has-pending-approval="hasPendingApprovalModel"
    v-model:has-pending-review="hasPendingReviewModel"
    :loading="loading"
    :failed="failed"
    :error-detail="errorDetail"
    :tasks="tasks"
    :publishers="publisherOptions"
    :publishers-loading="publishersLoading"
    :exporting="exporting"
    @load="load"
    @export="exportSection"
    @toggle-pending-approval="togglePendingApproval"
    @toggle-pending-review="togglePendingReview"
  />
</template>

<script setup lang="ts">
import type { SpaceAnalyticsTask } from '@/types/apiSpaces'
import type { AnalyticsSortOrder } from './utils'

import { ref, watch } from 'vue'

import { useAnalyticsPublishers } from '@/composables/useAnalyticsPublishers'

import { useAnalyticsExport } from './composables/useAnalyticsExport'
import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import TasksView from './TasksView.vue'
import { buildAnalyticsApiParams } from './utils'

import { SpacesApi } from '@/api/spaces'

// 管理员看板「题目」那一格（`/spaces/:id/analytics/tasks`）：取数与导出在这里，画法在
// `TasksView.vue`。这一层只做三件事 —— 拿 spaceId 取一次、筛选换了重取一次、几个筛选控件
// 写回筛选。判「失败/空/有数据」在画面那一半。
const { filters, replaceFilters, spaceId } = useSpaceAnalyticsFilters()
const { exporting, run: exportSection } = useAnalyticsExport('tasks', spaceId, filters)

const loading = ref(false)
// 读失败和「还没有题目」是两件事：失败替换掉表格，空状态才交给表格自己说。
const failed = ref(false)
const errorDetail = ref<string | null>(null)
const tasks = ref<SpaceAnalyticsTask[]>([])
const { publishers: publisherOptions, loading: publishersLoading } = useAnalyticsPublishers(spaceId, filters)

const publisherIdModel = ref<number | null>(filters.value.publisherId ?? null)
const sortByModel = ref(filters.value.sortBy || 'createdAt')
const sortOrderModel = ref<AnalyticsSortOrder>(filters.value.sortOrder || 'desc')
const hasPendingApprovalModel = ref(Boolean(filters.value.hasPendingApproval))
const hasPendingReviewModel = ref(Boolean(filters.value.hasPendingReview))

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
    const data = await SpacesApi.getAnalyticsTasks(spaceId.value, buildAnalyticsApiParams('tasks', filters.value))
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
</script>
