<script setup lang="ts">
import type { CreditUsage } from '@/lib/creditUsage'
import type { CreditRequest } from '@/types/creditOffers'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import CreditTopUp from '@/components/usage/CreditTopUp.vue'
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
  canManage?: boolean
  teamHandle?: string
  requests?: CreditRequest[]
  offersBusy?: boolean
  offersError?: string | null
}>()

const emit = defineEmits<{ retry: []; request: [kind: CreditRequest['kind'], credits?: number]; 'retry-offers': [] }>()

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
const otherCredits = computed(() =>
  (props.usage?.packs ?? []).filter((p) => !p.project_id).reduce((sum, p) => sum + p.credits_remaining, 0)
)
</script>

<template>
  <div class="tcv">
    <header class="tcv__actions">
      <BaseButton kind="secondary" :to="{ name: 'CreditPlans', query: teamHandle ? { team: teamHandle } : {} }">{{
        t('usage.plans.view')
      }}</BaseButton>
      <CreditTopUp
        v-if="canManage"
        :requests="requests"
        :busy="offersBusy"
        :error="offersError"
        @request="emit('request', 'top_up', $event)"
      />
    </header>
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
      <section class="tcv__balance">
        <UsagePeriodCard
          :period="usage.period"
          :plan="usage.plan"
          :month="month"
          :windows="usage.windows"
          :other-credits="otherCredits"
          :lines="usage.lines"
        />
      </section>
      <UsagePackList v-if="usage.packs.length" :packs="usage.packs" />
      <div class="tcv__columns">
        <UsageDailyChart :days="usage.days" stacked /><UsageShareList
          :title="t('usage.projects.byProject')"
          :items="projects"
        />
      </div>
      <details v-if="requests?.length" class="tcv__requests">
        <summary>{{ t('usage.offers.requestsTitle') }}</summary>
        <ul>
          <li v-for="item in requests" :key="item.id">
            <span
              >{{ t(item.kind === 'top_up' ? 'usage.offers.topupTitle' : 'usage.offers.teamApply')
              }}<template v-if="item.credits"> · {{ t('usage.points', { n: item.credits }) }}</template></span
            ><span>{{ t(`usage.offers.status.${item.status}`) }}</span>
          </li>
        </ul>
      </details>
    </template>
    <p v-if="offersError" class="tcv__error" role="alert">
      {{ offersError }} <BaseButton size="sm" @click="emit('retry-offers')">{{ t('usage.retry') }}</BaseButton>
    </p>
  </div>
</template>

<style scoped>
/* 列宽和内边距由外框 AppPage 的 `read` 档给，标题和正文同一条左沿。 */
.tcv {
  display: flex;
  flex-direction: column;
  gap: 32px;
}
.tcv__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.tcv__balance {
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}
.tcv__columns {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr);
  gap: 24px;
}
.tcv__columns > * {
  min-width: 0;
}
.tcv__requests {
  font-size: 13px;
  line-height: var(--lh-13);
}
.tcv__requests summary {
  cursor: pointer;
  color: var(--text);
}
.tcv__requests ul {
  list-style: none;
  padding: 0;
  margin-top: 12px;
}
.tcv__requests li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}
.tcv__error {
  color: var(--danger-ink);
  font-size: 13px;
  line-height: var(--lh-13);
}
@media (max-width: 767.98px) {
  .tcv__columns {
    grid-template-columns: minmax(0, 1fr);
  }
  .tcv__balance {
    padding: 16px;
  }
}
</style>
