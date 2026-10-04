<template>
  <div class="an-section">
    <div class="an-bar">
      <AnalyticsPublisherSelect v-model="publisherIdModel" :space-id="spaceId" :filters="filters" />
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
        @click="togglePendingApproval"
      >
        <v-icon size="16">{{ hasPendingApprovalModel ? 'mdi-checkbox-marked' : 'mdi-checkbox-blank-outline' }}</v-icon>
        {{ t('spaces.analytics.tasks.pendingClaims') }}
      </button>
      <button
        type="button"
        class="an-toggle"
        :class="{ 'an-toggle--on': hasPendingReviewModel }"
        :aria-pressed="hasPendingReviewModel"
        @click="togglePendingReview"
      >
        <v-icon size="16">{{ hasPendingReviewModel ? 'mdi-checkbox-marked' : 'mdi-checkbox-blank-outline' }}</v-icon>
        {{ t('spaces.analytics.tasks.pendingReviews') }}
      </button>
      <div class="an-bar__end">
        <AnalyticsExportButton
          section="tasks"
          :space-id="spaceId"
          :filters="filters"
          :label="t('spaces.analytics.tasks.export')"
        />
      </div>
    </div>

    <BaseLoadError v-if="failed" :title="t('spaces.analytics.tasks.loadFailed')" :error="errorDetail" @retry="load" />

    <div v-else class="an-table">
      <v-data-table :headers="headers" :items="tasks" :loading="loading" density="compact" items-per-page="10">
        <template #[`item.publisher`]="{ item }">{{ item.publisher?.name || '-' }}</template>
        <template #[`item.category`]="{ item }">{{ item.category?.name || '-' }}</template>
        <template #[`item.approved`]="{ item }">
          <span class="an-state" :class="approvalTone(item.approved)">{{
            t(`spaces.analytics.distribution.${item.approved}`)
          }}</span>
        </template>
        <template #[`item.createdAt`]="{ item }">{{ formatDate(item.createdAt) }}</template>
        <template #[`item.deadline`]="{ item }">{{ item.deadline ? formatDate(item.deadline) : '-' }}</template>
        <template #[`item.participantCount`]="{ item }">{{ formatCount(item.participantCount) }}</template>
        <template #[`item.pendingParticipantApprovalCount`]="{ item }">{{
          formatCount(item.pendingParticipantApprovalCount)
        }}</template>
        <template #[`item.pendingReviewCount`]="{ item }">{{ formatCount(item.pendingReviewCount) }}</template>
        <template #[`item.submissionConversionRate`]="{ item }">{{
          formatPercent(item.submissionConversionRate)
        }}</template>
        <template #[`item.successRate`]="{ item }">{{ formatPercent(item.successRate) }}</template>
      </v-data-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { SpaceAnalyticsTask } from '@/network/api/spaces/types'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AnalyticsExportButton from './components/AnalyticsExportButton.vue'
import AnalyticsPublisherSelect from './components/AnalyticsPublisherSelect.vue'
import { useAnalyticsOptions } from './composables/useAnalyticsOptions'
import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import { formatCount, formatDate, formatPercent } from './helpers'
import { buildAnalyticsApiParams } from './utils'

import BaseLoadError from '@/components/base/BaseLoadError.vue'
import { SpacesApi } from '@/network/api/spaces'

const { t } = useI18n()
const options = useAnalyticsOptions()
const { filters, replaceFilters, spaceId } = useSpaceAnalyticsFilters()

const loading = ref(false)
// 读失败和「还没有题目」是两件事：失败替换掉表格，空状态才交给表格自己说。
const failed = ref(false)
const errorDetail = ref<string | null>(null)
const tasks = ref<SpaceAnalyticsTask[]>([])
const publisherIdModel = ref<number | null>(filters.value.publisherId ?? null)
const sortByModel = ref(filters.value.sortBy || 'createdAt')
const sortOrderModel = ref(filters.value.sortOrder || 'desc')
const hasPendingApprovalModel = ref(Boolean(filters.value.hasPendingApproval))
const hasPendingReviewModel = ref(Boolean(filters.value.hasPendingReview))

watch(filters, (value) => {
  publisherIdModel.value = value.publisherId ?? null
  sortByModel.value = value.sortBy || 'createdAt'
  sortOrderModel.value = value.sortOrder || 'desc'
  hasPendingApprovalModel.value = Boolean(value.hasPendingApproval)
  hasPendingReviewModel.value = Boolean(value.hasPendingReview)
})

watch([publisherIdModel, sortByModel, sortOrderModel], async () => {
  await replaceFilters({
    publisherId: publisherIdModel.value ?? undefined,
    sortBy: sortByModel.value,
    sortOrder: sortOrderModel.value,
  })
})

const togglePendingApproval = async () => {
  hasPendingApprovalModel.value = !hasPendingApprovalModel.value
  await replaceFilters({
    hasPendingApproval: hasPendingApprovalModel.value || undefined,
  })
}

const togglePendingReview = async () => {
  hasPendingReviewModel.value = !hasPendingReviewModel.value
  await replaceFilters({
    hasPendingReview: hasPendingReviewModel.value || undefined,
  })
}

const load = async () => {
  loading.value = true
  failed.value = false
  errorDetail.value = null
  try {
    const { data } = await SpacesApi.getAnalyticsTasks(spaceId.value, buildAnalyticsApiParams('tasks', filters.value))
    tasks.value = data.tasks
  } catch (error) {
    console.error('load analytics tasks failed', error)
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

const headers = computed(() =>
  COLUMNS.map((key, index) => ({
    title: t(`spaces.analytics.tasks.col.${key}`),
    key,
    value: key,
    align: index === 0 ? ('start' as const) : ('center' as const),
  }))
)

const approvalTone = (value: string) =>
  value === 'APPROVED' ? 'an-state--ok' : value === 'DISAPPROVED' ? 'an-state--danger' : ''
</script>

<style scoped src="./analytics.css"></style>
