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

// 团队的「额度」：本月用了多少、其他额度、每天用量、各项目占多少。只有比例，也不按人分。
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
    share: p.share,
    to: { name: 'workspace-project', params: { projectId: p.id } },
  }))
)
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
      <UsagePeriodCard :period="usage.period" :plan-name="usage.plan.name" :month="month" :windows="usage.windows" />
      <UsagePackList v-if="usage.packs.length" :packs="usage.packs" />
      <UsageDailyChart :days="usage.days" />
      <UsageShareList :title="t('usage.projects.byProject')" :items="projects" />
    </template>
  </div>
</template>

<style scoped>
.tcv {
  display: flex;
  flex-direction: column;
  gap: 32px;
  max-width: var(--page-w);
  margin-inline: auto;
  padding: 24px 16px 48px;
}
</style>
