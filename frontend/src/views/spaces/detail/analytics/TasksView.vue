<template>
  <div class="an-section">
    <div class="an-bar">
      <AnalyticsPublisherSelect v-model="publisherId" :publishers="publishers" :loading="publishersLoading" />
      <v-select
        v-model="sortBy"
        autocomplete="off"
        :items="sortByItems"
        :prefix="t('spaces.analytics.sortBy')"
        :aria-label="t('spaces.analytics.sortBy')"
        density="compact"
        hide-details
        variant="outlined"
      />
      <v-select
        v-model="sortOrder"
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
        :class="{ 'an-toggle--on': hasPendingApproval }"
        :aria-pressed="hasPendingApproval"
        @click="emit('togglePendingApproval')"
      >
        <v-icon size="16">{{ hasPendingApproval ? 'mdi-checkbox-marked' : 'mdi-checkbox-blank-outline' }}</v-icon>
        {{ t('spaces.analytics.tasks.pendingClaims') }}
      </button>
      <button
        type="button"
        class="an-toggle"
        :class="{ 'an-toggle--on': hasPendingReview }"
        :aria-pressed="hasPendingReview"
        @click="emit('togglePendingReview')"
      >
        <v-icon size="16">{{ hasPendingReview ? 'mdi-checkbox-marked' : 'mdi-checkbox-blank-outline' }}</v-icon>
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
      @retry="emit('load')"
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
import type { SpaceAnalyticsTask } from '@/types/apiSpaces'
import type { AnalyticsSortOrder } from './utils'

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

// 管理员看板「题目」那一格的画法：一串筛选（出题人、排序、两个待办开关）加一张题目明细表。
// 取数与导出在页面里（`Tasks.vue`），这里只吃数据、往上报动作。
defineOptions({ name: 'TasksView' })

const props = defineProps<{
  loading: boolean
  /** 这一次没读到。和「还没有题目」是两件事：失败替换掉表格，空状态才交给表格自己说。 */
  failed: boolean
  /** 读失败时服务端原话；取不到时是 null。 */
  errorDetail: string | null
  tasks: SpaceAnalyticsTask[]
  /** 出题人下拉的选项：页面取回来，这里只画。 */
  publishers: Array<{ title: string; value: number | null }>
  publishersLoading: boolean
  /** 这一格正在导出。 */
  exporting: boolean
}>()

const publisherId = defineModel<number | null>('publisherId', { required: true })
const sortBy = defineModel<string>('sortBy', { required: true })
const sortOrder = defineModel<AnalyticsSortOrder>('sortOrder', { required: true })
const hasPendingApproval = defineModel<boolean>('hasPendingApproval', { required: true })
const hasPendingReview = defineModel<boolean>('hasPendingReview', { required: true })

const emit = defineEmits<{
  load: []
  /** 点了导出：报上去，拉文件在页面里做。 */
  export: []
  /** 翻一下「只看待审核报名」：换筛选在页面里（要写回地址）。 */
  togglePendingApproval: []
  /** 翻一下「只看待审核提交」。 */
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
