<template>
  <div class="an-section">
    <div class="an-bar">
      <AnalyticsPublisherSelect v-model="publisherIdModel" :space-id="spaceId" :filters="filters" />
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

    <template v-if="overview">
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
        <AnalyticsAlertGrid :alerts="alerts" @open="openTasks" />
      </section>
    </template>

    <BaseLoadError
      v-else-if="failed"
      :title="t('spaces.analytics.overview.loadFailed')"
      :error="errorDetail"
      @retry="load"
    />

    <p v-else-if="!loading" class="an-note">{{ t('spaces.analytics.overview.empty') }}</p>
  </div>
</template>

<script setup lang="ts">
import type { SpaceAnalyticsAlerts, SpaceAnalyticsOverview } from '@/network/api/spaces/types'
import type { AnalyticsGroupBy, SpaceAnalyticsQueryState } from './utils'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AnalyticsAlertGrid from './components/AnalyticsAlertGrid.vue'
import AnalyticsDistributionCard from './components/AnalyticsDistributionCard.vue'
import AnalyticsMetricCard from './components/AnalyticsMetricCard.vue'
import AnalyticsPublisherSelect from './components/AnalyticsPublisherSelect.vue'
import AnalyticsStatStrip from './components/AnalyticsStatStrip.vue'
import AnalyticsTrendCard from './components/AnalyticsTrendCard.vue'
import { useAnalyticsOptions } from './composables/useAnalyticsOptions'
import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import { formatCount, formatPercent, labelDistributionCodes, withDistributionPercent } from './helpers'
import { buildAnalyticsApiParams } from './utils'

import BaseLoadError from '@/components/base/BaseLoadError.vue'
import { ANALYTICS_ROUTE_NAMES } from '@/lib/spaceRouteNames'
import { SpacesApi } from '@/network/api/spaces'

const analyticsNames = ANALYTICS_ROUTE_NAMES
const { filters, pushToSection, replaceFilters, spaceId } = useSpaceAnalyticsFilters()

const { t, te } = useI18n()
const options = useAnalyticsOptions()

const loading = ref(false)
// 读失败和「还没有数据」是两件事：失败留在页面上（`failed`），空状态才交给「暂无」。
const failed = ref(false)
const errorDetail = ref<string | null>(null)
const overview = ref<SpaceAnalyticsOverview | null>(null)
const alerts = ref<SpaceAnalyticsAlerts | null>(null)
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

const metricCards = computed(() => {
  const o = overview.value
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
  overview.value
    ? {
        ...overview.value.taskDistributions.byCategory,
        items: withDistributionPercent(overview.value.taskDistributions.byCategory),
      }
    : null
)

const approvalDistribution = computed(() =>
  overview.value
    ? {
        ...overview.value.taskDistributions.byApprovalStatus,
        items: labelDistributionCodes(
          withDistributionPercent(overview.value.taskDistributions.byApprovalStatus),
          t,
          te
        ),
      }
    : null
)

const completionDistribution = computed(() =>
  overview.value
    ? {
        ...overview.value.taskDistributions.byCompletionStatus,
        items: labelDistributionCodes(
          withDistributionPercent(overview.value.taskDistributions.byCompletionStatus),
          t,
          te
        ),
      }
    : null
)

/** 点一个能处理的数：去「题目」那一格，带上对应的筛选。 */
const openTasks = (patch: Partial<SpaceAnalyticsQueryState>) => pushToSection(analyticsNames.tasks, patch)
</script>

<style scoped src="./analytics.css"></style>
