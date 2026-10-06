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
          :loading="exporting"
          :label="t('spaces.analytics.publishers.export')"
          @export="emit('export')"
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

    <BaseLoadError
      v-if="failed"
      :title="t('spaces.analytics.publishers.loadFailed')"
      :error="errorDetail"
      @retry="emit('retry')"
    />

    <BaseTable
      v-else
      class="an-table"
      :cols="COLUMN_WIDTHS"
      :label="t('spaces.analytics.publishers.tableLabel')"
      :loading="loading && !publishers.length"
      :busy="loading && !!publishers.length"
      :empty="!loading && !publishers.length ? t('spaces.analytics.publishers.empty') : null"
      :sort-key="table.sortKey.value"
      :sort-dir="table.sortDir.value"
      min-width="1080px"
      @sort="table.onSort"
    >
      <template #head>
        <tr>
          <BaseTableTh
            v-for="(key, index) in COLUMNS"
            :key="key"
            :sort-key="key"
            :align="index === 0 ? 'start' : 'center'"
          >
            {{ t(`spaces.analytics.publishers.col.${key}`) }}
          </BaseTableTh>
        </tr>
      </template>
      <tr v-for="item in table.pageRows.value" :key="item.publisherId">
        <td>{{ item.publisherName }}</td>
        <td class="an-c">{{ formatCount(item.taskCount) }}</td>
        <td class="an-c">{{ formatCount(item.participantCount) }}</td>
        <td class="an-c">{{ item.approvedParticipantCount }}</td>
        <td class="an-c">{{ item.submittedParticipantCount }}</td>
        <td class="an-c">{{ item.successfulParticipantCount }}</td>
        <td class="an-c">{{ item.avgParticipantsPerTask.toFixed(1) }}</td>
        <td class="an-c">{{ formatPercent(item.submissionConversionRate) }}</td>
        <td class="an-c">{{ formatPercent(item.successRate) }}</td>
        <td class="an-c">{{ item.lastTaskCreatedAt ? formatDate(item.lastTaskCreatedAt) : '-' }}</td>
      </tr>
      <template #foot>
        <TablePager v-model:page="table.page.value" :total="publishers.length" :per-page="table.perPage" />
      </template>
    </BaseTable>
  </div>
</template>

<script setup lang="ts">
// 出题人这一格的画面：排序、导出、排名条和一张表。读数和把排序写回地址归页面
// `Publishers.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type { SpaceAnalyticsPublisherMetrics } from '@/network/api/spaces/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AnalyticsExportButton from './components/AnalyticsExportButton.vue'
import AnalyticsMetricCard from './components/AnalyticsMetricCard.vue'
import AnalyticsStatStrip from './components/AnalyticsStatStrip.vue'
import { useAnalyticsOptions } from './composables/useAnalyticsOptions'
import { formatCount, formatDate, formatPercent } from './helpers'

import BaseLoadError from '@/components/base/BaseLoadError.vue'
import BaseTable from '@/components/base/BaseTable.vue'
import BaseTableTh from '@/components/base/BaseTableTh.vue'
import TablePager from '@/components/base/TablePager.vue'
import { useClientTable } from '@/components/base/tableSort'

const props = defineProps<{
  publishers: SpaceAnalyticsPublisherMetrics[]
  loading: boolean
  failed: boolean
  errorDetail: string | null
  exporting: boolean
}>()

const sortByModel = defineModel<string>('sortBy', { required: true })
const sortOrderModel = defineModel<string>('sortOrder', { required: true })

const emit = defineEmits<{
  retry: []
  export: []
}>()

const { t } = useI18n()
const options = useAnalyticsOptions()

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

// 第一列是出题人名字，吃剩下的宽度；其余是短值。
const COLUMN_WIDTHS = [null, '88px', '88px', '104px', '88px', '88px', '104px', '96px', '80px', '108px']

// 和原来 v-data-table 一样：每一列都能点表头排序，每页 10 条。
const table = useClientTable(computed(() => props.publishers))

const sortByItems = computed(() =>
  (['taskCount', 'participantCount', 'successRate', 'lastTaskCreatedAt'] as const).map((value) => ({
    title: t(`spaces.analytics.publishers.sort.${value}`),
    value,
  }))
)

const rankings = computed(() => {
  if (!props.publishers.length) return []
  const byTask = [...props.publishers].sort((a, b) => b.taskCount - a.taskCount)[0]
  const byRate = [...props.publishers].sort((a, b) => b.successRate - a.successRate)[0]
  const byParticipant = [...props.publishers].sort((a, b) => b.participantCount - a.participantCount)[0]

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
