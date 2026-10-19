<script setup lang="ts">
import type { Team } from '@/types'
import type { BetaCredits, CreditRequest, PublishedCreditPlan } from '@/types/creditOffers'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AppPage from '@/components/common/AppPage.vue'
import NavLink from '@/components/common/NavLink.vue'
import { fmtPoints } from '@/lib/creditUsage'

const props = defineProps<{
  plans: PublishedCreditPlan[]
  teams: Pick<Team, 'handle' | 'name'>[]
  teamHandle: string
  currentPlan: { key: string; name: string } | null
  beta: BetaCredits | null
  requests: CreditRequest[]
  canManage: boolean
  loading: boolean
  busy: boolean
  error: string | null
}>()
const emit = defineEmits<{ select: [handle: string]; request: []; retry: [] }>()
const { t, locale } = useI18n()
const availablePlans = computed(() =>
  props.plans.filter((plan) => plan.audience === 'both' || plan.audience === (props.teamHandle ? 'team' : 'personal'))
)
const pending = computed(() => props.requests.some((item) => item.kind === 'team_plan' && item.status === 'pending'))
const back = computed(() =>
  props.teamHandle
    ? { name: 'TeamsDetailCredits', params: { handle: props.teamHandle } }
    : { name: 'UserSettingsUsage' }
)
const terms = (plan: PublishedCreditPlan) =>
  plan.unlimited
    ? t('usage.plan.unlimited')
    : plan.windows.length
      ? t('usage.plans.windowTerms')
      : t('usage.plan.monthly', { n: fmtPoints(plan.credits_per_period ?? 0, locale.value) })
function windowTerms(window: PublishedCreditPlan['windows'][number]) {
  const period =
    window.calendar === 'week'
      ? t('usage.plan.week')
      : window.calendar === 'month'
        ? t('usage.plan.month')
        : t('usage.plan.hours', { hours: window.hours })
  return t('usage.plans.windowCredits', { period, n: fmtPoints(window.credits, locale.value) })
}
</script>

<template>
  <AppPage :title="t('usage.plans.title')" width="wide">
    <div class="plans-page">
      <NavLink class="plans-back" :to="back"
        ><v-icon icon="mdi-arrow-left" size="16" />{{ t('usage.plans.back') }}</NavLink
      >
      <header class="plans-lead">
        <div>
          <h2>{{ t('usage.plans.heading') }}</h2>
          <p>{{ t('usage.plans.lede') }}</p>
        </div>
        <BaseField class="plans-context" :label="t('usage.plans.forWhom')">
          <template #default="{ id }"
            ><select
              :id="id"
              :value="teamHandle"
              :disabled="busy || loading"
              @change="emit('select', ($event.target as HTMLSelectElement).value)"
            >
              <option value="">{{ t('usage.plans.personal') }}</option>
              <option v-for="team in teams" :key="team.handle" :value="team.handle">{{ team.name }}</option>
            </select></template
          >
        </BaseField>
      </header>
      <div v-if="currentPlan" class="plans-current">
        <span>{{ t('usage.plans.current') }}</span
        ><strong>{{ currentPlan.name }}</strong>
      </div>
      <v-skeleton-loader v-if="loading && !plans.length" type="article" />
      <BaseLoadError v-if="error" :title="t('usage.plans.loadFailed')" :error="error" @retry="emit('retry')" />
      <section v-if="availablePlans.length" class="plans-grid" :aria-label="t('usage.plans.title')">
        <article
          v-for="plan in availablePlans"
          :key="plan.key"
          class="plan-card"
          :class="{ 'plan-card--current': plan.key === currentPlan?.key }"
        >
          <header>
            <h3>{{ plan.name }}</h3>
            <span v-if="plan.key === currentPlan?.key" class="plan-current-tag">{{ t('usage.plans.current') }}</span>
          </header>
          <p class="plan-terms">{{ terms(plan) }}</p>
          <ul v-if="plan.windows.length" class="plan-windows">
            <li v-for="(window, index) in plan.windows" :key="index">{{ windowTerms(window) }}</li>
          </ul>
          <div class="plan-models">
            <h4>{{ t('usage.plan.models') }}</h4>
            <p>{{ plan.models.length ? plan.models.join(locale === 'en' ? ', ' : '、') : t('usage.plan.noModels') }}</p>
          </div>
        </article>
      </section>
      <section class="plans-team">
        <div class="plans-team__intro">
          <span class="plans-eyebrow">Team</span>
          <h2>{{ t('usage.offers.teamTitle') }}</h2>
          <p>{{ t('usage.offers.teamBody') }}</p>
          <p class="plans-privacy">
            <v-icon icon="mdi-shield-check-outline" size="16" />{{ t('usage.offers.privacy') }}
          </p>
        </div>
        <div class="plans-team__terms">
          <ul>
            <li><v-icon icon="mdi-check" size="17" />{{ t('usage.plans.sharedProjects') }}</li>
            <li><v-icon icon="mdi-check" size="17" />{{ t('usage.plans.projectBudgets') }}</li>
            <li><v-icon icon="mdi-check" size="17" />{{ t('usage.plans.memberLimits') }}</li>
            <li><v-icon icon="mdi-check" size="17" />{{ t('usage.plans.taskUsage') }}</li>
          </ul>
          <BaseButton
            v-if="teamHandle && canManage"
            kind="primary"
            :disabled="busy || loading || !!error || pending"
            :loading="busy"
            @click="emit('request')"
            >{{ t(pending ? 'usage.offers.pending' : 'usage.offers.teamApply') }}</BaseButton
          >
          <p v-else class="plans-application-note">
            <NavLink v-if="!teamHandle && !teams.length" :to="{ name: 'HomeTeamsExplore' }">{{
              t('home.nav.newTeam')
            }}</NavLink>
            <template v-else>{{ t(teamHandle ? 'usage.offers.managerOnly' : 'usage.plans.chooseTeam') }}</template>
          </p>
          <p class="plans-application-note">{{ t('usage.offers.quote') }}</p>
        </div>
      </section>
      <footer class="plans-activity">
        <v-icon icon="mdi-gift-outline" size="20" />
        <div>
          <strong>{{ t('usage.rewards.activity') }}</strong>
          <p>
            {{ t('usage.plans.activity', { beta: beta?.credits ?? 500, feedback: beta?.feedback_credits ?? 100 }) }}
          </p>
        </div>
        <NavLink to="/feedback#rewards"
          >{{ t('usage.rewards.activityDetails') }}<v-icon icon="mdi-chevron-right" size="16"
        /></NavLink>
      </footer>
    </div>
  </AppPage>
</template>

<style scoped>
.plans-page {
  padding: 24px 0 32px;
}
.plans-back {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  margin-bottom: 24px;
}
.plans-lead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 24px;
}
.plans-lead h2 {
  font-size: 23px;
  line-height: var(--lh-23);
  font-weight: 600;
  margin: 0;
}
.plans-lead p,
.plans-team__intro > p {
  color: var(--muted);
  font-size: 14px;
  line-height: var(--lh-14);
  margin: 8px 0 0;
}
.plans-context {
  display: grid;
  gap: 8px;
  min-width: 180px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.plans-context select {
  height: 36px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--text);
  padding: 0 8px;
}
.plans-current {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 24px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.plans-current strong {
  color: var(--ink);
  font-weight: 600;
}
.plans-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
}
.plan-card {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  padding: 24px;
}
.plan-card--current {
  border-color: var(--accent);
}
.plan-card header {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}
.plan-card h3 {
  margin: 0;
  font-size: 18px;
  line-height: var(--lh-18);
  font-weight: 600;
}
.plan-current-tag {
  color: var(--accent-ink);
  background: var(--accent-wash);
  border-radius: var(--radius-sm);
  padding: 4px 8px;
  font-size: 13px;
  line-height: var(--lh-13);
}
.plan-terms {
  color: var(--ink);
  font-size: 23px;
  line-height: var(--lh-23);
  margin: 24px 0 12px;
  font-weight: 500;
}
.plan-windows {
  padding-left: 16px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}
.plan-windows li + li {
  margin-top: 8px;
}
.plan-models {
  border-top: 1px solid var(--line);
  padding-top: 16px;
  margin-top: 16px;
}
.plan-models h4 {
  margin: 0 0 8px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 400;
}
.plan-models p {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}
.plans-team {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
  gap: 32px;
  border-top: 1px solid var(--line);
  padding-top: 24px;
  margin-top: 32px;
}
.plans-eyebrow {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.plans-team h2 {
  font-size: 18px;
  line-height: var(--lh-18);
  font-weight: 600;
  margin: 8px 0 16px;
}
.plans-team__intro > p {
  line-height: var(--lh-14-loose);
}
.plans-team__intro .plans-privacy {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  margin-top: 24px;
}
.plans-team__terms ul {
  list-style: none;
  padding: 0;
  margin: 0 0 24px;
}
.plans-team__terms li {
  display: flex;
  gap: 8px;
  align-items: center;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  margin-bottom: 12px;
}
.plans-team__terms li .v-icon {
  color: var(--muted);
}
.plans-application-note {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  margin: 12px 0 0;
}
.plans-activity {
  display: flex;
  gap: 12px;
  align-items: center;
  border-top: 1px solid var(--line);
  padding-top: 24px;
  margin-top: 32px;
  font-size: 13px;
  line-height: var(--lh-13);
}
.plans-activity > .v-icon {
  color: var(--accent-ink);
}
.plans-activity strong {
  font-weight: 500;
}
.plans-activity p {
  margin: 8px 0 0;
  color: var(--muted);
}
.plans-activity :deep(a) {
  display: flex;
  align-items: center;
  margin-left: auto;
  white-space: nowrap;
  color: var(--text);
}
@media (max-width: 767.98px) {
  .plans-context select {
    min-height: 44px;
  }
  .plans-lead {
    align-items: flex-start;
    flex-direction: column;
  }
  .plans-context {
    width: 100%;
  }
  .plans-team {
    grid-template-columns: minmax(0, 1fr);
    gap: 24px;
  }
  .plans-activity {
    flex-wrap: wrap;
  }
  .plans-activity :deep(a) {
    margin-left: 32px;
  }
}
</style>
