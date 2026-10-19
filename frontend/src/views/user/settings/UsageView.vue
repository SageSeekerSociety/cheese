<script setup lang="ts">
import type { CreditUsage, UsageTeam } from '@/lib/creditUsage'
import type { BetaCredits, CreditRewards } from '@/types/creditOffers'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import NavLink from '@/components/common/NavLink.vue'
import CreditOffers from '@/components/usage/CreditOffers.vue'
import UsageDailyChart from '@/components/usage/UsageDailyChart.vue'
import UsagePackList from '@/components/usage/UsagePackList.vue'
import UsagePeriodCard from '@/components/usage/UsagePeriodCard.vue'
import UsageShareList from '@/components/usage/UsageShareList.vue'
import UsageTeamList from '@/components/usage/UsageTeamList.vue'
import { fmtMonth } from '@/lib/creditUsage'

// 个人设置里的「芝士额度」：本月用了多少（按协作、问答、写作、算力分）、其他额度、每天用量、
// 我名下的项目各占多少、我所在的团队还剩多少。
defineOptions({ name: 'UsageSettingsView' })

const props = defineProps<{
  usage: CreditUsage | null
  loading: boolean
  error: string | null
  beta?: BetaCredits | null
  offersBusy?: boolean
  offersError?: string | null
  rewards?: CreditRewards | null
}>()

const emit = defineEmits<{ retry: []; claim: []; 'retry-offers': [] }>()

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

const teamLink = (team: UsageTeam) => ({ name: 'TeamsDetailCredits', params: { handle: team.handle } })
</script>

<template>
  <div class="settings-page">
    <header class="usv__head">
      <h1 class="t-page-title">{{ t('usage.title') }}</h1>
      <BaseButton kind="secondary" :to="{ name: 'CreditPlans' }">{{ t('usage.plans.view') }}</BaseButton>
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
      <section class="settings-card usv__card">
        <UsagePeriodCard
          :period="usage.period"
          :plan="usage.plan"
          :other-credits="otherCredits"
          :month="month"
          :lines="usage.lines"
          :windows="usage.windows"
        />
        <CreditOffers
          :beta="beta"
          :busy="offersBusy"
          :error="offersError"
          @claim="emit('claim')"
          @retry="emit('retry-offers')"
        />
      </section>

      <section v-if="usage.packs.length" class="settings-card usv__card">
        <UsagePackList :packs="usage.packs" />
      </section>

      <section class="settings-card usv__card">
        <UsageDailyChart :days="usage.days" stacked />
      </section>

      <section class="settings-card usv__card">
        <UsageShareList :title="t('usage.projects.mine')" :items="projects" />
      </section>

      <section v-if="usage.teams?.length" class="settings-card usv__card">
        <UsageTeamList :teams="usage.teams" :link-of="teamLink" />
      </section>
      <section class="usv__rewards">
        <div class="usv__reward-head">
          <h2 class="t-title">{{ t('usage.rewards.history') }}</h2>
          <NavLink to="/feedback">{{ t('usage.offers.feedbackLink') }}</NavLink>
        </div>
        <p v-if="rewards" class="usv__earned">{{ t('usage.rewards.earned', { n: rewards.feedback_credits }) }}</p>
        <ul v-if="rewards?.items.length" class="usv__history">
          <li v-for="(item, index) in rewards.items" :key="index">
            <span>{{ t(item.kind === 'beta' ? 'usage.rewards.betaTitle' : 'usage.rewards.accepted') }}</span
            ><time>{{ new Date(item.created_at).toLocaleDateString(locale) }}</time
            ><strong>{{ t('usage.rewards.gain', { n: item.credits }) }}</strong>
          </li>
        </ul>
        <BaseEmptyState v-else-if="rewards" size="inline" :title="t('usage.rewards.empty')" />
      </section>
    </template>
  </div>
</template>

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
.usv__head,
.usv__reward-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.usv__rewards {
  padding-top: 8px;
}
.usv__reward-head :deep(a),
.usv__earned {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.usv__earned {
  margin: 12px 0;
}
.usv__history {
  padding: 0;
  margin: 12px 0 0;
  list-style: none;
}
.usv__history li {
  display: flex;
  gap: 16px;
  align-items: center;
  border-top: 1px solid var(--line);
  padding: 12px 0;
  font-size: 13px;
  line-height: var(--lh-13);
}
.usv__history time {
  color: var(--muted);
  margin-left: auto;
}
.usv__history strong {
  color: var(--ok-ink);
  font-weight: 500;
}
.usv__card {
  padding: 20px 24px;
}

/* 断点对齐共享 token（`styles/breakpoints.scss`）：700 → 767.98，和设置页一起
   加载的 `settings-card.css` 同一条线。 */
@media (max-width: 767.98px) {
  .usv__card {
    padding: 16px;
  }
}
</style>
