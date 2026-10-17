<template>
  <div class="an-section">
    <div class="an-bar">
      <AnalyticsPublisherSelect v-model="publisherId" :publishers="publishers" :loading="publishersLoading" />
      <v-select
        v-model="groupBy"
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
      @retry="emit('load')"
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
        <AnalyticsAlertGrid :alerts="alerts" @open="emit('openTasks', $event)" />
      </section>
    </template>

    <BaseEmptyState v-else-if="!loading" size="inline" :title="t('spaces.analytics.overview.empty')" />
  </div>
</template>

<script setup lang="ts">
import type { SpaceAnalyticsAlerts, SpaceAnalyticsOverview } from '@/types/apiSpaces'
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

// 管理员看板「总览」那一格的画法：读数、按类别/审核/完成的分布、两条趋势，外加一组告警。
// 取数在页面里（`Overview.vue`），这里只吃数据、往上报动作。
defineOptions({ name: 'OverviewView' })

const props = defineProps<{
  loading: boolean
  /** 这一次没读到。和「还没有数据」是两件事：失败留在页面上，空状态才说「暂无」。 */
  failed: boolean
  /** 读失败时服务端原话；取不到时是 null。 */
  errorDetail: string | null
  overview: SpaceAnalyticsOverview | null
  alerts: SpaceAnalyticsAlerts | null
  /** 出题人下拉的选项：页面取回来，这里只画。 */
  publishers: Array<{ title: string; value: number | null }>
  publishersLoading: boolean
}>()

const publisherId = defineModel<number | null>('publisherId', { required: true })
const groupBy = defineModel<AnalyticsGroupBy>('groupBy', { required: true })

const emit = defineEmits<{
  load: []
  /** 点一个能处理的数：报上去「要跳到哪一格、带哪几个筛选」。 */
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
