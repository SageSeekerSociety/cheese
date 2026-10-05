<template>
  <div class="an-section">
    <div class="an-bar">
      <AnalyticsPublisherSelect v-model="publisherIdModel" :items="publisherItems" :loading="publishersLoading" />
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
          :loading="exporting"
          :label="t('spaces.analytics.participants.export')"
          @export="emit('export')"
        />
      </div>
    </div>

    <v-progress-linear v-if="loading && !participants" indeterminate color="primary" />

    <!-- A failed reload must replace the block, not leave the previous filter's numbers standing (docs/design-system.md §3.10). -->
    <BaseLoadError
      v-if="failed"
      :title="t('spaces.analytics.participants.loadFailed')"
      :error="errorDetail"
      @retry="emit('retry')"
    />

    <template v-else-if="participants">
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

    <BaseEmptyState v-else-if="!loading" size="inline" :title="t('spaces.analytics.participants.empty')" />
  </div>
</template>

<script setup lang="ts">
// 参与者这一格的画面：一排筛选、指标条、趋势、分布。读数和把筛选写回地址归页面
// `Participants.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type { SpaceAnalyticsParticipants } from '@/network/api/spaces/types'
import type { AnalyticsGroupBy, AnalyticsRealNameFilter } from './utils'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AnalyticsDistributionCard from './components/AnalyticsDistributionCard.vue'
import AnalyticsExportButton from './components/AnalyticsExportButton.vue'
import AnalyticsMetricCard from './components/AnalyticsMetricCard.vue'
import AnalyticsPublisherSelect from './components/AnalyticsPublisherSelect.vue'
import AnalyticsStatStrip from './components/AnalyticsStatStrip.vue'
import AnalyticsTrendCard from './components/AnalyticsTrendCard.vue'
import { useAnalyticsOptions } from './composables/useAnalyticsOptions'
import { formatCount, labelDistributionCodes, withDistributionPercent } from './helpers'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'

const props = defineProps<{
  participants: SpaceAnalyticsParticipants | null
  loading: boolean
  failed: boolean
  errorDetail: string | null
  exporting: boolean
  publisherItems: Array<{ title: string; value: number | null }>
  publishersLoading: boolean
}>()

const publisherIdModel = defineModel<number | null>('publisherId', { required: true })
const participationApprovedModel = defineModel<string | null>('participationApproved', { required: true })
const completionStatusModel = defineModel<string | null>('completionStatus', { required: true })
const realNameModel = defineModel<AnalyticsRealNameFilter>('realName', { required: true })
const groupByModel = defineModel<AnalyticsGroupBy>('groupBy', { required: true })

const emit = defineEmits<{
  retry: []
  export: []
}>()

const { t, te } = useI18n()
const options = useAnalyticsOptions()

const realNameItems = computed(() =>
  (['all', 'with', 'without'] as const).map((value) => ({
    title: t(`spaces.analytics.participants.realNameOption.${value}`),
    value,
  }))
)

const metrics = computed(() => {
  const p = props.participants
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
  const d = props.participants?.distributions
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
