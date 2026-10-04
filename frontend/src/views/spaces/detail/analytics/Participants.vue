<template>
  <div class="an-section">
    <div class="an-bar">
      <AnalyticsPublisherSelect v-model="publisherIdModel" :space-id="spaceId" :filters="filters" />
      <v-select
        v-model="participationApprovedModel"
        autocomplete="off"
        :items="options.approval.value"
        :prefix="t('spaces.analytics.participants.approval')"
        :aria-label="t('spaces.analytics.participants.approval')"
        density="compact"
        hide-details
        variant="outlined"
      />
      <v-select
        v-model="completionStatusModel"
        autocomplete="off"
        :items="options.completion.value"
        :prefix="t('spaces.analytics.participants.completion')"
        :aria-label="t('spaces.analytics.participants.completion')"
        density="compact"
        hide-details
        variant="outlined"
      />
      <v-select
        v-model="realNameModel"
        autocomplete="off"
        :items="realNameItems"
        :prefix="t('spaces.analytics.participants.realName')"
        :aria-label="t('spaces.analytics.participants.realName')"
        density="compact"
        hide-details
        variant="outlined"
      />
      <v-select
        v-model="groupByModel"
        autocomplete="off"
        :items="options.groupBy.value"
        :prefix="t('spaces.analytics.groupBy.label')"
        :aria-label="t('spaces.analytics.groupBy.label')"
        density="compact"
        hide-details
        variant="outlined"
      />
      <div class="an-bar__end">
        <AnalyticsExportButton
          section="participants"
          :space-id="spaceId"
          :filters="filters"
          :label="t('spaces.analytics.participants.export')"
        />
      </div>
    </div>

    <v-progress-linear v-if="loading && !participants" indeterminate color="primary" />

    <template v-if="participants">
      <AnalyticsStatStrip>
        <AnalyticsMetricCard
          v-for="item in metrics"
          :key="item.key"
          :label="t(`spaces.analytics.participants.metric.${item.key}.label`)"
          :value="formatCount(item.value)"
          :description="t(`spaces.analytics.participants.metric.${item.key}.hint`)"
        />
      </AnalyticsStatStrip>

      <div class="an-grid">
        <AnalyticsTrendCard
          :title="t('spaces.analytics.participants.trend.joined')"
          :points="participants.trends.participantsJoined"
        />
        <AnalyticsTrendCard
          :title="t('spaces.analytics.participants.trend.submitted')"
          :points="participants.trends.submissionsCreated"
        />
        <AnalyticsTrendCard
          :title="t('spaces.analytics.participants.trend.succeeded')"
          :points="participants.trends.successesAchieved"
        />
      </div>

      <div class="an-grid">
        <AnalyticsDistributionCard
          v-for="item in distributions"
          :key="item.key"
          :title="t(`spaces.analytics.participants.distribution.${item.key}`)"
          :distribution="item.distribution"
        />
      </div>
    </template>

    <LoadErrorNotice
      v-else-if="failed"
      :title="t('spaces.analytics.participants.loadFailed')"
      :error="errorDetail"
      @retry="load"
    />

    <p v-else-if="!loading" class="an-note">{{ t('spaces.analytics.participants.empty') }}</p>
  </div>
</template>

<script setup lang="ts">
import type { SpaceAnalyticsParticipants } from '@/network/api/spaces/types'
import type { AnalyticsGroupBy, AnalyticsRealNameFilter } from './utils'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AnalyticsDistributionCard from './components/AnalyticsDistributionCard.vue'
import AnalyticsExportButton from './components/AnalyticsExportButton.vue'
import AnalyticsMetricCard from './components/AnalyticsMetricCard.vue'
import AnalyticsPublisherSelect from './components/AnalyticsPublisherSelect.vue'
import AnalyticsStatStrip from './components/AnalyticsStatStrip.vue'
import AnalyticsTrendCard from './components/AnalyticsTrendCard.vue'
import { useAnalyticsOptions } from './composables/useAnalyticsOptions'
import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import { formatCount, labelDistributionCodes, withDistributionPercent } from './helpers'
import { buildAnalyticsApiParams } from './utils'

import LoadErrorNotice from '@/components/common/LoadErrorNotice.vue'
import { SpacesApi } from '@/network/api/spaces'

const { t, te } = useI18n()
const options = useAnalyticsOptions()
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

const realNameItems = computed(() =>
  (['all', 'with', 'without'] as const).map((value) => ({
    title: t(`spaces.analytics.participants.realNameOption.${value}`),
    value,
  }))
)

const metrics = computed(() => {
  const p = participants.value
  if (!p) return []
  return [
    { key: 'claims', value: p.entityMetrics.participantCount },
    { key: 'approved', value: p.entityMetrics.approvedParticipantCount },
    { key: 'succeeded', value: p.entityMetrics.successfulParticipantCount },
    { key: 'realName', value: p.studentMetrics.studentsWithRealNameCount },
  ]
})

/** 状态码那三张换成名字；年级、专业、班级本来就是名字。 */
const distributions = computed(() => {
  const d = participants.value?.distributions
  if (!d) return []
  const codes = (value: typeof d.byApprovalStatus) => ({
    ...value,
    items: labelDistributionCodes(withDistributionPercent(value), t, te),
  })
  const names = (value: typeof d.byGrade) => ({ ...value, items: withDistributionPercent(value) })
  return [
    { key: 'approval', distribution: codes(d.byApprovalStatus) },
    { key: 'completion', distribution: codes(d.byCompletionStatus) },
    { key: 'realName', distribution: codes(d.byRealNameStatus) },
    { key: 'grade', distribution: names(d.byGrade) },
    { key: 'major', distribution: names(d.byMajor) },
    { key: 'className', distribution: names(d.byClassName) },
  ]
})
</script>

<style scoped src="./analytics.css"></style>
