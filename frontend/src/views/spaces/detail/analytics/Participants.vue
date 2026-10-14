<template>
  <ParticipantsView
    v-model:publisher-id="publisherIdModel"
    v-model:participation-approved="participationApprovedModel"
    v-model:completion-status="completionStatusModel"
    v-model:real-name="realNameModel"
    v-model:group-by="groupByModel"
    :loading="loading"
    :failed="failed"
    :error-detail="errorDetail"
    :participants="participants"
    :publishers="publisherOptions"
    :publishers-loading="publishersLoading"
    :exporting="exporting"
    @load="load"
    @export="exportSection"
  />
</template>

<script setup lang="ts">
import type { SpaceAnalyticsParticipants } from '@/types/apiSpaces'
import type {
  AnalyticsApproveFilter,
  AnalyticsCompletionStatus,
  AnalyticsGroupBy,
  AnalyticsRealNameFilter,
} from './utils'

import { ref, watch } from 'vue'

import { useAnalyticsPublishers } from '@/composables/useAnalyticsPublishers'

import { useAnalyticsExport } from './composables/useAnalyticsExport'
import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import ParticipantsView from './ParticipantsView.vue'
import { buildAnalyticsApiParams } from './utils'

import { SpacesApi } from '@/api/spaces'

// 管理员看板「参与者」那一格（`/spaces/:id/analytics/participants`）：取数与导出在这里，
// 画法在 `ParticipantsView.vue`。这一层只做三件事 —— 拿 spaceId 取一次、筛选换了重取一次、
// 五个下拉写回筛选。判「失败/空/有数据」在画面那一半。
const { filters, replaceFilters, spaceId } = useSpaceAnalyticsFilters()
const { exporting, run: exportSection } = useAnalyticsExport('participants', spaceId, filters)

const loading = ref(false)
// 读失败和「还没有参与者」是两件事：失败留在页面上，空状态才说「暂无」。
const failed = ref(false)
const errorDetail = ref<string | null>(null)
const participants = ref<SpaceAnalyticsParticipants | null>(null)
const { publishers: publisherOptions, loading: publishersLoading } = useAnalyticsPublishers(spaceId, filters)

const publisherIdModel = ref<number | null>(filters.value.publisherId ?? null)
const participationApprovedModel = ref<AnalyticsApproveFilter | null>(filters.value.participationApproved ?? null)
const completionStatusModel = ref<AnalyticsCompletionStatus | null>(filters.value.completionStatus ?? null)
const realNameModel = ref<AnalyticsRealNameFilter>(filters.value.realName)
const groupByModel = ref<AnalyticsGroupBy>(filters.value.groupBy)

watch(filters, (value) => {
  publisherIdModel.value = value.publisherId ?? null
  participationApprovedModel.value = value.participationApproved ?? null
  completionStatusModel.value = value.completionStatus ?? null
  realNameModel.value = value.realName
  groupByModel.value = value.groupBy
})

watch([publisherIdModel, participationApprovedModel, completionStatusModel, realNameModel, groupByModel], async () => {
  await replaceFilters({
    publisherId: publisherIdModel.value ?? undefined,
    participationApproved: participationApprovedModel.value || undefined,
    completionStatus: completionStatusModel.value || undefined,
    realName: realNameModel.value,
    groupBy: groupByModel.value,
  })
})

const load = async () => {
  loading.value = true
  failed.value = false
  errorDetail.value = null
  try {
    const data = await SpacesApi.getAnalyticsParticipants(
      spaceId.value,
      buildAnalyticsApiParams('participants', filters.value)
    )
    participants.value = data
  } catch (error) {
    console.error('load analytics participants failed', error)
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
