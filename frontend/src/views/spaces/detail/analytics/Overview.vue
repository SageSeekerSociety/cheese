<template>
  <OverviewView
    v-model:publisher-id="publisherIdModel"
    v-model:group-by="groupByModel"
    :loading="loading"
    :failed="failed"
    :error-detail="errorDetail"
    :overview="overview"
    :alerts="alerts"
    :publishers="publisherOptions"
    :publishers-loading="publishersLoading"
    @load="load"
    @open-tasks="openTasks"
  />
</template>

<script setup lang="ts">
import type { SpaceAnalyticsAlerts, SpaceAnalyticsOverview } from '@/types/apiSpaces'
import type { AnalyticsGroupBy, SpaceAnalyticsQueryState } from './utils'

import { ref, watch } from 'vue'

import { useAnalyticsPublishers } from '@/composables/useAnalyticsPublishers'

import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import OverviewView from './OverviewView.vue'
import { buildAnalyticsApiParams } from './utils'

import { SpacesApi } from '@/api/spaces'
import { ANALYTICS_ROUTE_NAMES } from '@/lib/spaceRouteNames'

// 管理员看板「总览」那一格（`/spaces/:id/analytics`）：取数在这里，画法在 `OverviewView.vue`。
// 这一层只做四件事 —— 拿 spaceId 取一次、空间或筛选换了重取一次、两个下拉写回筛选、点一个
// 能处理的数时跳去「题目」那一格。判「失败/空/有数据」在画面那一半。
const analyticsNames = ANALYTICS_ROUTE_NAMES
const { filters, pushToSection, replaceFilters, spaceId } = useSpaceAnalyticsFilters()

const loading = ref(false)
// 读失败和「还没有数据」是两件事：失败留在页面上（`failed`），空状态才交给「暂无」。
const failed = ref(false)
const errorDetail = ref<string | null>(null)
const overview = ref<SpaceAnalyticsOverview | null>(null)
const alerts = ref<SpaceAnalyticsAlerts | null>(null)
const { publishers: publisherOptions, loading: publishersLoading } = useAnalyticsPublishers(spaceId, filters)

const publisherIdModel = ref<number | null>(filters.value.publisherId ?? null)
const groupByModel = ref<AnalyticsGroupBy>(filters.value.groupBy)

watch(
  filters,
  (value) => {
    publisherIdModel.value = value.publisherId ?? null
    groupByModel.value = value.groupBy
  },
  { immediate: true }
)

watch([publisherIdModel, groupByModel], async () => {
  await replaceFilters({
    publisherId: publisherIdModel.value ?? undefined,
    groupBy: groupByModel.value,
  })
})

const load = async () => {
  loading.value = true
  failed.value = false
  errorDetail.value = null
  try {
    const [overviewResponse, alertsResponse] = await Promise.all([
      SpacesApi.getAnalyticsOverview(spaceId.value, buildAnalyticsApiParams('overview', filters.value)),
      SpacesApi.getAnalyticsAlerts(spaceId.value),
    ])

    overview.value = overviewResponse
    alerts.value = alertsResponse
  } catch (error) {
    console.error('load overview analytics failed', error)
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

/** 点一个能处理的数：去「题目」那一格，带上对应的筛选。 */
const openTasks = (patch: Partial<SpaceAnalyticsQueryState>) => pushToSection(analyticsNames.tasks, patch)
</script>
