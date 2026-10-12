<script setup lang="ts">
import type { SpaceAnalyticsAlerts } from '@/types/apiSpaces'
import type { SpaceAnalyticsQueryState } from './utils'

import { ref, watch } from 'vue'

import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import AlertsView from './AlertsView.vue'

import { SpacesApi } from '@/api/spaces'
import { ANALYTICS_ROUTE_NAMES } from '@/lib/spaceRouteNames'

// 管理员看板「告警」那一格（`/spaces/:id/analytics/alerts`）：取数在这里，画法在
// `AlertsView.vue`。这一层只做三件事 —— 拿 spaceId 取一次、空间换了重取一次、点一个能
// 处理的数时带着筛选跳去「题目」那一格。判「失败/空/有数据」在画面那一半。
const { pushToSection, spaceId } = useSpaceAnalyticsFilters()

const loading = ref(false)
// 读失败和「还没有告警」是两件事：失败留在页面上，空状态才说「暂无」。
const failed = ref(false)
const errorDetail = ref<string | null>(null)
const alerts = ref<SpaceAnalyticsAlerts | null>(null)

const load = async () => {
  loading.value = true
  failed.value = false
  errorDetail.value = null
  try {
    const data = await SpacesApi.getAnalyticsAlerts(spaceId.value)
    alerts.value = data
  } catch (error) {
    console.error('load analytics alerts failed', error)
    failed.value = true
    errorDetail.value = error instanceof Error && error.message ? error.message : null
  } finally {
    loading.value = false
  }
}

watch(
  spaceId,
  () => {
    load().catch(() => undefined)
  },
  { immediate: true }
)

/** 点一个能处理的数：去「题目」那一格，带上对应的筛选。 */
const openTasks = (patch: Partial<SpaceAnalyticsQueryState>) => pushToSection(ANALYTICS_ROUTE_NAMES.tasks, patch)
</script>

<template>
  <AlertsView
    :loading="loading"
    :failed="failed"
    :error-detail="errorDetail"
    :alerts="alerts"
    @load="load"
    @open-tasks="openTasks"
  />
</template>
