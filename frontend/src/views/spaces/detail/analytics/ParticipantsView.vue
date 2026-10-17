<template>
  <div class="an-section">
    <div class="an-bar">
      <AnalyticsPublisherSelect v-model="publisherId" :publishers="publishers" :loading="publishersLoading" />
      <v-select
        v-model="participationApproved"
        autocomplete="off"
        :items="options.approval.value"
        :prefix="t('spaces.analytics.participants.approval')"
        :aria-label="t('spaces.analytics.participants.approval')"
        density="compact"
        hide-details
        variant="outlined"
      />
      <v-select
        v-model="completionStatus"
        autocomplete="off"
        :items="options.completion.value"
        :prefix="t('spaces.analytics.participants.completion')"
        :aria-label="t('spaces.analytics.participants.completion')"
        density="compact"
        hide-details
        variant="outlined"
      />
      <v-select
        v-model="realName"
        autocomplete="off"
        :items="realNameItems"
        :prefix="t('spaces.analytics.participants.realName')"
        :aria-label="t('spaces.analytics.participants.realName')"
        density="compact"
        hide-details
        variant="outlined"
      />
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
      @retry="emit('load')"
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
import type { SpaceAnalyticsParticipants } from '@/types/apiSpaces'
import type {
  AnalyticsApproveFilter,
  AnalyticsCompletionStatus,
  AnalyticsGroupBy,
  AnalyticsRealNameFilter,
} from './utils'

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

// 管理员看板「参与者」那一格的画法：几个读数、三条趋势、六张分布（审核、完成、
// 实名，年级、专业、班级）。取数与导出在页面里（`Participants.vue`），这里只吃数据、
// 往上报动作。
defineOptions({ name: 'ParticipantsView' })

const props = defineProps<{
  loading: boolean
  /** 这一次没读到。和「还没有参与者」是两件事：失败留在页面上，空状态才说「暂无」。 */
  failed: boolean
  /** 读失败时服务端原话；取不到时是 null。 */
  errorDetail: string | null
  participants: SpaceAnalyticsParticipants | null
  /** 出题人下拉的选项：页面取回来，这里只画。 */
  publishers: Array<{ title: string; value: number | null }>
  publishersLoading: boolean
  /** 这一格正在导出。 */
  exporting: boolean
}>()

const publisherId = defineModel<number | null>('publisherId', { required: true })
const participationApproved = defineModel<AnalyticsApproveFilter | null>('participationApproved', { required: true })
const completionStatus = defineModel<AnalyticsCompletionStatus | null>('completionStatus', { required: true })
const realName = defineModel<AnalyticsRealNameFilter>('realName', { required: true })
const groupBy = defineModel<AnalyticsGroupBy>('groupBy', { required: true })

const emit = defineEmits<{
  load: []
  /** 点了导出：报上去，拉文件在页面里做。 */
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
