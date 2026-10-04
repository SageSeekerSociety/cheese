<template>
  <div class="an-section">
    <div class="an-bar">
      <v-select
        v-model="sortByModel"
        autocomplete="off"
        :items="sortByItems"
        :prefix="t('spaces.analytics.sortBy')"
        :aria-label="t('spaces.analytics.sortBy')"
        density="compact"
        hide-details
        variant="outlined"
      />
      <v-select
        v-model="sortOrderModel"
        autocomplete="off"
        :items="options.sortOrder.value"
        :aria-label="t('spaces.analytics.sortOrder.label')"
        density="compact"
        hide-details
        variant="outlined"
        class="an-bar__narrow"
      />
      <div class="an-bar__end">
        <AnalyticsExportButton
          section="publishers"
          :space-id="spaceId"
          :filters="filters"
          :label="t('spaces.analytics.publishers.export')"
        />
      </div>
    </div>

    <AnalyticsStatStrip v-if="rankings.length">
      <AnalyticsMetricCard
        v-for="item in rankings"
        :key="item.label"
        :label="item.label"
        :value="item.name"
        :description="item.meta"
      />
    </AnalyticsStatStrip>

    <LoadErrorNotice
      v-if="failed"
      :title="t('spaces.analytics.publishers.loadFailed')"
      :error="errorDetail"
      @retry="load"
    />

    <div v-else class="an-table">
      <v-data-table :headers="headers" :items="publishers" :loading="loading" density="compact" items-per-page="10">
        <template #[`item.taskCount`]="{ item }">{{ formatCount(item.taskCount) }}</template>
        <template #[`item.participantCount`]="{ item }">{{ formatCount(item.participantCount) }}</template>
        <template #[`item.avgParticipantsPerTask`]="{ item }">{{ item.avgParticipantsPerTask.toFixed(1) }}</template>
        <template #[`item.submissionConversionRate`]="{ item }">{{
          formatPercent(item.submissionConversionRate)
        }}</template>
        <template #[`item.successRate`]="{ item }">{{ formatPercent(item.successRate) }}</template>
        <template #[`item.lastTaskCreatedAt`]="{ item }">
          {{ item.lastTaskCreatedAt ? formatDate(item.lastTaskCreatedAt) : '-' }}
        </template>
      </v-data-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { SpaceAnalyticsPublisherMetrics } from '@/network/api/spaces/types'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AnalyticsExportButton from './components/AnalyticsExportButton.vue'
import AnalyticsMetricCard from './components/AnalyticsMetricCard.vue'
import AnalyticsStatStrip from './components/AnalyticsStatStrip.vue'
import { useAnalyticsOptions } from './composables/useAnalyticsOptions'
import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import { formatCount, formatDate, formatPercent } from './helpers'
import { buildAnalyticsApiParams } from './utils'

import LoadErrorNotice from '@/components/common/LoadErrorNotice.vue'
import { SpacesApi } from '@/network/api/spaces'

const { t } = useI18n()
const options = useAnalyticsOptions()
const { filters, replaceFilters, spaceId } = useSpaceAnalyticsFilters()

const loading = ref(false)
// 读失败和「还没有出题人」是两件事：失败替换掉表格，空状态才交给表格自己说。
const failed = ref(false)
const errorDetail = ref<string | null>(null)
const publishers = ref<SpaceAnalyticsPublisherMetrics[]>([])
const sortByModel = ref(filters.value.sortBy || 'taskCount')
const sortOrderModel = ref(filters.value.sortOrder || 'desc')

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

const COLUMNS = [
  'publisherName',
  'taskCount',
  'participantCount',
  'approvedParticipantCount',
  'submittedParticipantCount',
  'successfulParticipantCount',
  'avgParticipantsPerTask',
  'submissionConversionRate',
  'successRate',
  'lastTaskCreatedAt',
] as const

const headers = computed(() =>
  COLUMNS.map((key, index) => ({
    title: t(`spaces.analytics.publishers.col.${key}`),
    key,
    value: key,
    align: index === 0 ? ('start' as const) : ('center' as const),
  }))
)

const sortByItems = computed(() =>
  (['taskCount', 'participantCount', 'successRate', 'lastTaskCreatedAt'] as const).map((value) => ({
    title: t(`spaces.analytics.publishers.sort.${value}`),
    value,
  }))
)

const rankings = computed(() => {
  if (!publishers.value.length) return []
  const byTask = [...publishers.value].sort((a, b) => b.taskCount - a.taskCount)[0]
  const byRate = [...publishers.value].sort((a, b) => b.successRate - a.successRate)[0]
  const byParticipant = [...publishers.value].sort((a, b) => b.participantCount - a.participantCount)[0]

  return [
    {
      label: t('spaces.analytics.publishers.rank.mostTasks'),
      name: byTask?.publisherName || '-',
      meta: t('spaces.analytics.publishers.rank.tasks', { n: formatCount(byTask?.taskCount) }),
    },
    {
      label: t('spaces.analytics.publishers.rank.bestRate'),
      name: byRate?.publisherName || '-',
      meta: formatPercent(byRate?.successRate),
    },
    {
      label: t('spaces.analytics.publishers.rank.mostClaims'),
      name: byParticipant?.publisherName || '-',
      meta: t('spaces.analytics.publishers.rank.claims', { n: formatCount(byParticipant?.participantCount) }),
    },
  ]
})
</script>

<style scoped src="./analytics.css"></style>
