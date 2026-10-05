<template>
  <OverviewView
    v-model:publisher-id="publisherIdModel"
    v-model:group-by="groupByModel"
    :overview="overview"
    :alerts="alerts"
    :loading="loading"
    :failed="failed"
    :error-detail="errorDetail"
    :publisher-items="publisherItems"
    :publishers-loading="publishersLoading"
    @retry="load"
    @open-tasks="openTasks"
  />
</template>

<script setup lang="ts">
// 总览这一格的容器：读地址里的筛选、拉总览与告警、按筛选拉出题人下拉。画面在
// `OverviewView.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type { SpaceAnalyticsAlerts, SpaceAnalyticsOverview } from '@/network/api/spaces/types'
import type { AnalyticsGroupBy, SpaceAnalyticsQueryState } from './utils'

import { ref, watch } from 'vue'

import { useAnalyticsPublishers } from './composables/useAnalyticsPublishers'
import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import OverviewView from './OverviewView.vue'
import { buildAnalyticsApiParams } from './utils'

import { ANALYTICS_ROUTE_NAMES } from '@/lib/spaceRouteNames'
import { SpacesApi } from '@/network/api/spaces'

const { filters, pushToSection, replaceFilters, spaceId } = useSpaceAnalyticsFilters()

const loading = ref(false)
// 读失败和「还没有数据」是两件事：失败留在页面上（`failed`），空状态才交给「暂无」。
const failed = ref(false)
const errorDetail = ref<string | null>(null)
const overview = ref<SpaceAnalyticsOverview | null>(null)
const alerts = ref<SpaceAnalyticsAlerts | null>(null)
const publisherIdModel = ref<number | null>(filters.value.publisherId ?? null)
const groupByModel = ref<AnalyticsGroupBy>(filters.value.groupBy)

const { items: publisherItems, loading: publishersLoading } = useAnalyticsPublishers(spaceId, filters)

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

    overview.value = overviewResponse.data
    alerts.value = alertsResponse.data
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
const openTasks = (patch: Partial<SpaceAnalyticsQueryState>) => pushToSection(ANALYTICS_ROUTE_NAMES.tasks, patch)
</script>
