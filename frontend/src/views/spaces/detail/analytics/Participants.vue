<template>
  <ParticipantsView
    v-model:publisher-id="publisherIdModel"
    v-model:participation-approved="participationApprovedModel"
    v-model:completion-status="completionStatusModel"
    v-model:real-name="realNameModel"
    v-model:group-by="groupByModel"
    :participants="participants"
    :loading="loading"
    :failed="failed"
    :error-detail="errorDetail"
    :exporting="exporting"
    :publisher-items="publisherItems"
    :publishers-loading="publishersLoading"
    @retry="load"
    @export="onExport"
  />
</template>

<script setup lang="ts">
// 参与者这一格的容器：读地址筛选、拉数据、按筛选拉出题人列表、导出 CSV。画面在
// `ParticipantsView.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type { SpaceAnalyticsParticipants } from '@/network/api/spaces/types'
import type { AnalyticsGroupBy, AnalyticsRealNameFilter } from './utils'

import { ref, watch } from 'vue'

import { useAnalyticsExport } from './composables/useAnalyticsExport'
import { useAnalyticsPublishers } from './composables/useAnalyticsPublishers'
import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import ParticipantsView from './ParticipantsView.vue'
import { buildAnalyticsApiParams } from './utils'

import { SpacesApi } from '@/network/api/spaces'

const { filters, replaceFilters, spaceId } = useSpaceAnalyticsFilters()

const loading = ref(false)
// 读失败和「还没有参与者」是两件事：失败留在页面上，空状态才说「暂无」。
const failed = ref(false)
const errorDetail = ref<string | null>(null)
const participants = ref<SpaceAnalyticsParticipants | null>(null)
const publisherIdModel = ref<number | null>(filters.value.publisherId ?? null)
const participationApprovedModel = ref(filters.value.participationApproved ?? null)
const completionStatusModel = ref(filters.value.completionStatus ?? null)
const realNameModel = ref<AnalyticsRealNameFilter>(filters.value.realName)
const groupByModel = ref<AnalyticsGroupBy>(filters.value.groupBy)

const { items: publisherItems, loading: publishersLoading } = useAnalyticsPublishers(spaceId, filters)
const { exporting, exportCsv } = useAnalyticsExport(spaceId, filters)

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
    const { data } = await SpacesApi.getAnalyticsParticipants(
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

const onExport = () => {
  exportCsv('participants').catch(() => undefined)
}
</script>
