<template>
  <div class="an-section">
    <div class="an-bar">
      <AnalyticsPublisherSelect v-model="publisherIdModel" :items="publisherItems" :loading="publishersLoading" />
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
      <button
        type="button"
        class="an-toggle"
        :class="{ 'an-toggle--on': hasPendingApprovalModel }"
        :aria-pressed="hasPendingApprovalModel"
        @click="emit('togglePendingApproval')"
      >
        <v-icon size="16">{{ hasPendingApprovalModel ? 'mdi-checkbox-marked' : 'mdi-checkbox-blank-outline' }}</v-icon>
        {{ t('spaces.analytics.tasks.pendingClaims') }}
      </button>
      <button
        type="button"
        class="an-toggle"
        :class="{ 'an-toggle--on': hasPendingReviewModel }"
        :aria-pressed="hasPendingReviewModel"
        @click="emit('togglePendingReview')"
      >
        <v-icon size="16">{{ hasPendingReviewModel ? 'mdi-checkbox-marked' : 'mdi-checkbox-blank-outline' }}</v-icon>
        {{ t('spaces.analytics.tasks.pendingReviews') }}
      </button>
      <div class="an-bar__end">
        <AnalyticsExportButton
          :loading="exporting"
          :label="t('spaces.analytics.tasks.export')"
          @export="emit('export')"
        />
      </div>
    </div>

    <BaseLoadError
      v-if="failed"
      :title="t('spaces.analytics.tasks.loadFailed')"
      :error="errorDetail"
      @retry="emit('retry')"
    />

    <BaseTable
      v-else
      class="an-table"
      :cols="COLUMN_WIDTHS"
      :label="t('spaces.analytics.tasks.tableLabel')"
      :loading="loading && !tasks.length"
      :busy="loading && !!tasks.length"
      :empty="!loading && !tasks.length ? t('spaces.analytics.tasks.empty') : null"
      :sort-key="table.sortKey.value"
      :sort-dir="table.sortDir.value"
      min-width="1180px"
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
            {{ t(`spaces.analytics.tasks.col.${key}`) }}
          </BaseTableTh>
        </tr>
      </template>
      <tr v-for="item in table.pageRows.value" :key="item.taskId">
        <td>{{ item.taskName }}</td>
        <td class="an-c">{{ item.publisher?.name || '-' }}</td>
        <td class="an-c">{{ item.category?.name || '-' }}</td>
        <td class="an-c">
          <span class="an-state" :class="approvalTone(item.approved)">{{
            t(`spaces.analytics.distribution.${item.approved}`)
          }}</span>
        </td>
        <td class="an-c">{{ formatDate(item.createdAt) }}</td>
        <td class="an-c">{{ item.deadline ? formatDate(item.deadline) : '-' }}</td>
        <td class="an-c">{{ formatCount(item.participantCount) }}</td>
        <td class="an-c">{{ formatCount(item.pendingParticipantApprovalCount) }}</td>
        <td class="an-c">{{ formatCount(item.pendingReviewCount) }}</td>
        <td class="an-c">{{ formatPercent(item.submissionConversionRate) }}</td>
        <td class="an-c">{{ formatPercent(item.successRate) }}</td>
      </tr>
      <template #foot>
        <TablePager v-model:page="table.page.value" :total="tasks.length" :per-page="table.perPage" />
      </template>
    </BaseTable>
  </div>
</template>

<script setup lang="ts">
// 题目这一格的画面：一排筛选、两个待办开关、一张表。读数和把筛选写回地址归页面
// `Tasks.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type { SpaceAnalyticsTask } from '@/network/api/spaces/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AnalyticsExportButton from './components/AnalyticsExportButton.vue'
import AnalyticsPublisherSelect from './components/AnalyticsPublisherSelect.vue'
import { useAnalyticsOptions } from './composables/useAnalyticsOptions'
import { formatCount, formatDate, formatPercent } from './helpers'

import BaseLoadError from '@/components/base/BaseLoadError.vue'
import BaseTable from '@/components/base/BaseTable.vue'
import BaseTableTh from '@/components/base/BaseTableTh.vue'
import TablePager from '@/components/base/TablePager.vue'
import { useClientTable } from '@/components/base/tableSort'

const props = defineProps<{
  tasks: SpaceAnalyticsTask[]
  loading: boolean
  failed: boolean
  errorDetail: string | null
  exporting: boolean
  publisherItems: Array<{ title: string; value: number | null }>
  publishersLoading: boolean
}>()

const publisherIdModel = defineModel<number | null>('publisherId', { required: true })
const sortByModel = defineModel<string>('sortBy', { required: true })
const sortOrderModel = defineModel<string>('sortOrder', { required: true })
const hasPendingApprovalModel = defineModel<boolean>('hasPendingApproval', { required: true })
const hasPendingReviewModel = defineModel<boolean>('hasPendingReview', { required: true })

const emit = defineEmits<{
  retry: []
  export: []
  togglePendingApproval: []
  togglePendingReview: []
}>()

const { t } = useI18n()
const options = useAnalyticsOptions()

const sortByItems = computed(() =>
  (['createdAt', 'participantCount', 'successRate', 'pendingReviewCount'] as const).map((value) => ({
    title: t(`spaces.analytics.tasks.sort.${value}`),
    value,
  }))
)

const COLUMNS = [
  'taskName',
  'publisher',
  'category',
  'approved',
  'createdAt',
  'deadline',
  'participantCount',
  'pendingParticipantApprovalCount',
  'pendingReviewCount',
  'submissionConversionRate',
  'successRate',
] as const

// 第一列是题目名，吃剩下的宽度；其余是短值。
const COLUMN_WIDTHS = [null, '120px', '100px', '96px', '108px', '108px', '88px', '96px', '80px', '96px', '80px']

// 和原来 v-data-table 一样：每一列都能点表头排序，每页 10 条。出题人、分类按名字排。
const table = useClientTable(
  computed(() => props.tasks),
  {
    pick: (row, key) =>
      key === 'publisher'
        ? row.publisher?.name
        : key === 'category'
          ? row.category?.name
          : row[key as keyof typeof row],
  }
)

const approvalTone = (value: string) =>
  value === 'APPROVED' ? 'an-state--ok' : value === 'DISAPPROVED' ? 'an-state--danger' : ''
</script>

<style scoped src="./analytics.css"></style>
