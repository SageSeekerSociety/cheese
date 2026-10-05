<script setup lang="ts">
import type { CreditUsage } from '@/lib/creditUsage'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import UsageDailyChart from '@/components/usage/UsageDailyChart.vue'
import UsagePackList from '@/components/usage/UsagePackList.vue'
import UsagePeriodCard from '@/components/usage/UsagePeriodCard.vue'
import UsageShareList from '@/components/usage/UsageShareList.vue'
import { fmtMonth } from '@/lib/creditUsage'

// 团队的「额度」：本月用了多少（按协作、算力分）、其他额度、每天用量、各项目占多少。不按人分。
defineOptions({ name: 'TeamCreditsView' })

const props = defineProps<{
  usage: CreditUsage | null
  loading: boolean
  error: string | null
}>()

const emit = defineEmits<{ retry: [] }>()

const { t, locale } = useI18n()

const month = computed(() => (props.usage ? fmtMonth(props.usage.days, locale.value) : ''))
const projects = computed(() =>
  (props.usage?.projects ?? []).map((p) => ({
    id: p.id,
    name: p.name ?? t('usage.projects.unnamed'),
    credits: p.credits,
    to: { name: 'workspace-project', params: { projectId: p.id } },
  }))
)

/** 方案之外的额度还剩多少点。 */
const otherCredits = computed(() => (props.usage?.packs ?? []).reduce((sum, p) => sum + p.credits_remaining, 0))
</script>

<template>
  <div class="tcv">
    <v-skeleton-loader v-if="loading && !usage" type="article, image" />

    <AdminEmptyState
      v-else-if="error && !usage"
      tone="error"
      :title="t('usage.loadFailed')"
      :desc="error"
      :action="t('usage.retry')"
      @action="emit('retry')"
    />

    <template v-else-if="usage">
      <UsagePeriodCard
        :period="usage.period"
        :plan="usage.plan"
        :month="month"
        :windows="usage.windows"
        :other-credits="otherCredits"
        :lines="usage.lines"
      />
      <UsagePackList v-if="usage.packs.length" :packs="usage.packs" />
      <UsageDailyChart :days="usage.days" stacked />
      <UsageShareList :title="t('usage.projects.byProject')" :items="projects" />
    </template>
  </div>
</template>

<style scoped>
/* 列宽和内边距由外框 AppPage 的 `read` 档给，标题和正文同一条左沿。 */
.tcv {
  display: flex;
  flex-direction: column;
  gap: 32px;
}
</style>
