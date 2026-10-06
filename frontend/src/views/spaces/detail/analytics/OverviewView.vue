<template>
  <div class="an-section">
    <div class="an-bar">
      <AnalyticsPublisherSelect v-model="publisherIdModel" :items="publisherItems" :loading="publishersLoading" />
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
    </div>

    <v-progress-linear v-if="loading && !overview" indeterminate color="primary" />

    <!-- A failed reload must replace the block, not leave the previous filter's numbers standing (docs/design-system.md §3.10). -->
    <BaseLoadError
      v-if="failed"
      :title="t('spaces.analytics.overview.loadFailed')"
      :error="errorDetail"
      @retry="emit('retry')"
    />

    <template v-else-if="overview">
      <AnalyticsStatStrip>
        <AnalyticsMetricCard
          v-for="item in metricCards"
          :key="item.label"
          :label="item.label"
          :value="item.value"
          :description="item.description"
        />
      </AnalyticsStatStrip>

      <div class="an-grid">
        <AnalyticsTrendCard
          :title="t('spaces.analytics.overview.trend.joined')"
          :points="overview.trends.participantsJoined"
        />
        <AnalyticsTrendCard
          :title="t('spaces.analytics.overview.trend.succeeded')"
          :points="overview.trends.successesAchieved"
        />
      </div>

      <div class="an-grid">
        <AnalyticsDistributionCard
          :title="t('spaces.analytics.overview.distribution.category')"
          :distribution="categoryDistribution"
        />
        <AnalyticsDistributionCard
          :title="t('spaces.analytics.overview.distribution.approval')"
          :distribution="approvalDistribution"
        />
        <AnalyticsDistributionCard
          :title="t('spaces.analytics.overview.distribution.completion')"
          :distribution="completionDistribution"
        />
      </div>

      <section v-if="alerts">
        <h3 class="an-card__title">{{ t('spaces.analytics.overview.alerts') }}</h3>
        <AnalyticsAlertGrid :alerts="alerts" @open="(patch) => emit('openTasks', patch)" />
      </section>
    </template>

    <BaseEmptyState v-else-if="!loading" size="inline" :title="t('spaces.analytics.overview.empty')" />
  </div>
</template>

<script setup lang="ts">
// 总览这一格的画面：指标条、两张趋势、三张分布、能点开的告警。读数是页面
// `Overview.vue` 的事，这里只收读到的数据和两份筛选，把「重试」「去题目」发出去
// （场景规则见 docs/manual/dev/scenes.md）。
import type { SpaceAnalyticsAlerts, SpaceAnalyticsOverview } from '@/network/api/spaces/types'
import type { AnalyticsGroupBy, SpaceAnalyticsQueryState } from './utils'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AnalyticsAlertGrid from './components/AnalyticsAlertGrid.vue'
import AnalyticsDistributionCard from './components/AnalyticsDistributionCard.vue'
import AnalyticsMetricCard from './components/AnalyticsMetricCard.vue'
import AnalyticsPublisherSelect from './components/AnalyticsPublisherSelect.vue'
import AnalyticsStatStrip from './components/AnalyticsStatStrip.vue'
import AnalyticsTrendCard from './components/AnalyticsTrendCard.vue'
import { useAnalyticsOptions } from './composables/useAnalyticsOptions'
import { formatCount, formatPercent, labelDistributionCodes, withDistributionPercent } from './helpers'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'

const props = defineProps<{
  overview: SpaceAnalyticsOverview | null
  alerts: SpaceAnalyticsAlerts | null
  loading: boolean
  failed: boolean
  errorDetail: string | null
  publisherItems: Array<{ title: string; value: number | null }>
  publishersLoading: boolean
}>()

const publisherIdModel = defineModel<number | null>('publisherId', { required: true })
const groupByModel = defineModel<AnalyticsGroupBy>('groupBy', { required: true })

const emit = defineEmits<{
  retry: []
  openTasks: [patch: Partial<SpaceAnalyticsQueryState>]
}>()

const { t, te } = useI18n()
const options = useAnalyticsOptions()

const metricCards = computed(() => {
  const o = props.overview
  if (!o) return []
  const m = o.entityMetrics
  return [
    { key: 'tasks', value: m.taskCount },
    { key: 'publishers', value: m.publisherCount },
    { key: 'claims', value: m.participantCount },
    { key: 'submitted', value: m.submittedParticipantCount, rate: m.submissionConversionRate },
    { key: 'succeeded', value: m.successfulParticipantCount, rate: m.successRate },
    { key: 'members', value: o.studentMetrics.studentCount },
  ].map((item) => ({
    label: t(`spaces.analytics.overview.metric.${item.key}.label`),
    value: formatCount(item.value),
    description: t(`spaces.analytics.overview.metric.${item.key}.hint`, { rate: formatPercent(item.rate) }),
  }))
})

const categoryDistribution = computed(() =>
  props.overview
    ? {
        ...props.overview.taskDistributions.byCategory,
        items: withDistributionPercent(props.overview.taskDistributions.byCategory),
      }
    : null
)

const approvalDistribution = computed(() =>
  props.overview
    ? {
        ...props.overview.taskDistributions.byApprovalStatus,
        items: labelDistributionCodes(
          withDistributionPercent(props.overview.taskDistributions.byApprovalStatus),
          t,
          te
        ),
      }
    : null
)

const completionDistribution = computed(() =>
  props.overview
    ? {
        ...props.overview.taskDistributions.byCompletionStatus,
        items: labelDistributionCodes(
          withDistributionPercent(props.overview.taskDistributions.byCompletionStatus),
          t,
          te
        ),
      }
    : null
)
</script>

<style scoped src="./analytics.css"></style>
