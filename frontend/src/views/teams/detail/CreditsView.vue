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
import CreditWarning from '@/components/usage/CreditWarning.vue'
import UsageDailyChart from '@/components/usage/UsageDailyChart.vue'
import UsagePackList from '@/components/usage/UsagePackList.vue'
import UsagePeriodCard from '@/components/usage/UsagePeriodCard.vue'
import UsageShareList from '@/components/usage/UsageShareList.vue'
import UsageTeamList from '@/components/usage/UsageTeamList.vue'
import { fmtMonth, fmtPoints, fmtResetAt } from '@/lib/creditUsage'

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

// 额度用到八成时页面顶部那一条。和 `usage.alerts` 的判定同一档：团队方案额度
// 按月看的看本月方案包，按窗口的看最满的那个窗口；到 100% 时页面画的是「已用完」，
// 这一条就退场，两种说法不会同时出现。
const windowBodyKey = (w: { hours: number | null; calendar: 'week' | 'month' | null }) =>
  w.calendar === 'week'
    ? 'usage.warning.teamWeekBody'
    : w.calendar === 'month'
      ? 'usage.warning.teamMonthBody'
      : 'usage.warning.teamHoursBody'

const warning = computed(() => {
  const usage = props.usage
  if (!usage) return null
  const period = usage.period
  if (
    period &&
    !period.unlimited &&
    period.remaining_ratio !== null &&
    period.remaining_ratio > 0 &&
    period.remaining_ratio <= 0.2
  ) {
    const left = Math.max(0, (period.credits_total ?? 0) - (period.credits_used ?? 0))
    return {
      title: t('usage.warning.teamTitle'),
      body: t('usage.warning.teamBody', {
        n: fmtPoints(left, locale.value),
        at: period.resets_at ? fmtResetAt(period.resets_at, locale.value) : '',
      }),
    }
  }
  const fullest = (usage.windows ?? []).reduce<(typeof usage.windows)[number] | null>(
    (best, window) => (best === null || window.used_ratio > best.used_ratio ? window : best),
    null
  )
  if (fullest && fullest.used_ratio >= 0.8 && fullest.used_ratio < 1) {
    return {
      title: t('usage.warning.teamTitle'),
      body: t(windowBodyKey(fullest), {
        n: fmtPoints(Math.max(0, fullest.credits_total - fullest.credits_used), locale.value),
        hours: fullest.hours ?? 0,
        at: fullest.resets_at ? fmtResetAt(fullest.resets_at, locale.value) : '',
      }),
    }
  }
  return null
})
</script>

<template>
  <div class="tcv settings-page">
    <header class="settings-page__head">
      <div>
        <h1 class="t-page-title">{{ t('home.nav.teamCredits') }}</h1>
        <p v-if="personal" class="settings-page__lede">{{ t('teams.settings.creditsLedePersonal') }}</p>
      </div>
      <div class="settings-page__actions">
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
      </div>
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
        <CreditWarning v-if="warning" :title="warning.title" :body="warning.body" />
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

<style scoped src="@/styles/settings-card.css"></style>

<style scoped>
/* 列宽和水平内边距由设置那一层的内容列给（SettingsOverlay 的 `.so__content`），标题和正文同一条左沿。 */
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
  /* 右边留出说明图标那一格：只有「+0 点」那一行有图标，但每行都留，日期和点数才对得齐。 */
  position: relative;
  display: flex;
  gap: 16px;
  align-items: center;
  border-top: 1px solid var(--line);
  padding: 12px 28px 12px 0;
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
  display: inline-flex;
  align-items: center;
  justify-content: center;
  position: absolute;
  top: 50%;
  right: 0;
  width: 20px;
  height: 20px;
  padding: 0;
  transform: translateY(-50%);
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
