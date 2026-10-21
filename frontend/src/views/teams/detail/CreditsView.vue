<script setup lang="ts">
import type { CreditUsage, UsageTeam } from '@/lib/creditUsage'
import type { BetaCredits, CreditRequest, CreditRewards } from '@/types/creditOffers'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import NavLink from '@/components/common/NavLink.vue'
import CreditOffers from '@/components/usage/CreditOffers.vue'
import CreditTopUp from '@/components/usage/CreditTopUp.vue'
import UsageDailyChart from '@/components/usage/UsageDailyChart.vue'
import UsagePackList from '@/components/usage/UsagePackList.vue'
import UsagePeriodCard from '@/components/usage/UsagePeriodCard.vue'
import UsageShareList from '@/components/usage/UsageShareList.vue'
import UsageTeamList from '@/components/usage/UsageTeamList.vue'
import { fmtMonth } from '@/lib/creditUsage'

// 团队的「额度」：本月用了多少（按协作、算力分）、其他额度、每天用量、各项目占多少。不按人分。
defineOptions({ name: 'TeamCreditsView' })

const props = defineProps<{
  usage: CreditUsage | null
  loading: boolean
  error: string | null
  canManage?: boolean
  personal?: boolean
  beta?: BetaCredits | null
  rewards?: CreditRewards | null
  teamHandle?: string
  requests?: CreditRequest[]
  offersBusy?: boolean
  offersError?: string | null
}>()

const emit = defineEmits<{
  retry: []
  claim: []
  request: [kind: CreditRequest['kind'], credits?: number]
  'retry-offers': []
}>()

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

const REWARD_LABEL: Record<CreditRewards['items'][number]['kind'], string> = {
  beta: 'usage.rewards.betaTitle',
  feedback: 'usage.rewards.accepted',
  invite: 'usage.rewards.invite',
}

/** 方案之外的额度还剩多少点。 */
const otherCredits = computed(() =>
  (props.usage?.packs ?? []).filter((p) => !p.project_id).reduce((sum, p) => sum + p.credits_remaining, 0)
)
const teamLink = (team: UsageTeam) => ({ name: 'TeamsDetailCredits', params: { handle: team.handle } })
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
        <CreditOffers
          v-if="personal"
          :beta="beta"
          :busy="offersBusy"
          :error="offersError"
          @claim="emit('claim')"
          @retry="emit('retry-offers')"
        />
      </section>
      <UsagePackList v-if="usage.packs.length" :packs="usage.packs" />
      <div class="tcv__columns">
        <UsageDailyChart :days="usage.days" stacked /><UsageShareList
          :title="t(personal ? 'usage.projects.mine' : 'usage.projects.byProject')"
          :items="projects"
        />
      </div>
      <UsageTeamList v-if="personal && usage.teams?.length" :teams="usage.teams" :link-of="teamLink" />
      <section v-if="personal" class="usv__rewards">
        <div class="usv__reward-head">
          <h2 class="t-title">{{ t('usage.rewards.history') }}</h2>
          <NavLink to="/feedback">{{ t('usage.offers.feedbackLink') }}</NavLink>
        </div>
        <p v-if="rewards" class="usv__earned">
          {{ t('usage.rewards.earned', { n: rewards.feedback_credits })
          }}<template v-if="rewards.invite_credits">
            · {{ t('usage.rewards.inviteEarned', { n: rewards.invite_credits }) }}</template
          >
        </p>
        <ul v-if="rewards?.items.length" class="usv__history">
          <li v-for="(item, index) in rewards.items" :key="index">
            <span>{{ t(REWARD_LABEL[item.kind]) }}</span
            ><time>{{ new Date(item.created_at).toLocaleDateString(locale) }}</time
            ><strong>{{ t('usage.rewards.gain', { n: item.credits }) }}</strong>
            <!-- 本月邀请额度已满之后首次采纳的好友：这一行照记，但是 0 点，旁边说一句为什么。
                 open-on-click 让触屏点得开，按钮本身让键盘够得着。 -->
            <v-tooltip
              v-if="item.kind === 'invite' && !item.credits"
              :text="t('usage.rewards.inviteCapped')"
              location="top"
              open-on-click
              :open-delay="0"
            >
              <template #activator="{ props: tip }">
                <button
                  type="button"
                  class="usv__why tap-target"
                  v-bind="tip"
                  :aria-label="t('usage.rewards.inviteCapped')"
                >
                  <span class="mdi mdi-information-outline" aria-hidden="true" />
                </button>
              </template>
            </v-tooltip>
          </li>
        </ul>
        <BaseEmptyState v-else-if="rewards" size="inline" :title="t('usage.rewards.empty')" />
      </section>
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
/* 「+0 点」旁边那颗说明：20px 的图标，手指要点得中（tap-target，§3.6）；字色同 --muted。 */
.usv__why {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  margin-left: -12px;
  padding: 0;
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 14px;
  cursor: pointer;
  transition: color var(--dur-quick) var(--ease-standard);
}
.usv__why:hover,
.usv__why:focus-visible {
  color: var(--text);
}
.usv__why:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
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
