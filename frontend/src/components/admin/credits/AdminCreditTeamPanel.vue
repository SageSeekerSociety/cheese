<script setup lang="ts">
import type { CreditAudit, CreditPack, CreditTeamDetail, Plan } from '@/lib/adminCredits'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDisplay } from 'vuetify'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminFlash from '@/components/admin/AdminFlash.vue'
import AdminMeterBar from '@/components/admin/AdminMeterBar.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { fmtCredits, fmtDate, fmtDateTime, fmtMonth, meterTone, teamTitle } from '@/lib/adminCredits'

// 一个团队的额度：挂哪个方案、手上每一笔额度用了多少、管理员对它做过什么。
const props = withDefaults(
  defineProps<{
    modelValue: boolean
    team: CreditTeamDetail | null
    loading?: boolean
    error?: string | null
    plans: Plan[]
    planSaving?: boolean
    /** 改方案被拒的原话（例如方案只给个人）；`null` = 没出错。 */
    planError?: string | null
    history: CreditAudit[] | null
    historyError?: string | null
  }>(),
  { loading: false, error: null, planSaving: false, planError: null, historyError: null }
)

const emit = defineEmits<{
  'update:modelValue': [open: boolean]
  'change-plan': [planKey: string]
  'dismiss-plan-error': []
  grant: []
  retry: []
}>()

const { t, locale } = useI18n()
const { mdAndUp } = useDisplay()

const width = computed(() => (mdAndUp.value ? 520 : undefined))
const planByKey = computed(() => new Map(props.plans.map((plan) => [plan.key, plan])))
const planOptions = computed(() => props.plans.map((plan) => ({ value: plan.key, title: plan.name })))

const title = computed(() => {
  const team = props.team
  if (!team) return ''
  return teamTitle(team)
})

const meta = computed(() => {
  const team = props.team
  if (!team) return ''
  if (team.personal_owner) return `${t('credits.teams.kindPersonal')} · @${team.personal_owner}`
  return team.member_count === null
    ? t('credits.teams.kindTeam')
    : `${t('credits.teams.kindTeam')} · ${t('credits.teams.members', { n: team.member_count })}`
})

function packName(pack: CreditPack): string {
  if (pack.source === 'plan_period') {
    return t('credits.panel.packPlan', { month: fmtMonth(pack.period_start ?? pack.created_at, locale.value) })
  }
  if (pack.source === 'admin_grant') return t('credits.panel.packGrant')
  if (pack.source === 'purchase') return t('credits.panel.packPurchase')
  if (pack.source === 'task_earmark') {
    return pack.task_name ? t('credits.panel.packTask', { task: pack.task_name }) : t('credits.panel.packTaskUnknown')
  }
  return t('credits.panel.packOther')
}

function packMeta(pack: CreditPack): string {
  const parts = [
    pack.expires_at
      ? t('credits.panel.expires', { date: fmtDate(pack.expires_at, locale.value) })
      : t('credits.panel.noExpiry'),
  ]
  if (pack.project_name) parts.unshift(t('credits.panel.project', { project: pack.project_name }))
  if (pack.reason) parts.push(pack.reason)
  return parts.join(' · ')
}

function ratio(pack: CreditPack): number {
  return pack.credits_total > 0 ? Math.min(1, pack.credits_used / pack.credits_total) : 1
}

function planName(key: unknown): string {
  return typeof key === 'string' ? planByKey.value.get(key)?.name ?? key : '—'
}

function historyLine(entry: CreditAudit): string {
  if (entry.action === 'team.plan') {
    return t('credits.panel.logPlan', { from: planName(entry.before?.plan_key), to: planName(entry.after?.plan_key) })
  }
  if (entry.action === 'team.grant') {
    const credits = Number(entry.after?.credits_total ?? 0)
    const expires = entry.after?.expires_at
    const expiry =
      typeof expires === 'string'
        ? t('credits.panel.expires', { date: fmtDate(expires, locale.value) })
        : t('credits.panel.noExpiry')
    return t('credits.panel.logGrant', { credits: fmtCredits(credits, locale.value), expiry })
  }
  return entry.action
}
</script>

<template>
  <v-navigation-drawer
    :model-value="modelValue"
    location="right"
    temporary
    :width="width"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="actp">
      <header class="actp__head">
        <div class="actp__id">
          <span class="actp__title t-title">{{ title }}</span>
          <span class="actp__meta t-meta-read">{{ meta }}</span>
        </div>
        <BaseButton
          icon="mdi-close"
          size="sm"
          :aria-label="t('credits.panel.close')"
          @click="emit('update:modelValue', false)"
        />
      </header>

      <div class="actp__body">
        <v-skeleton-loader v-if="loading && !team" type="list-item-two-line, list-item-two-line" />

        <AdminEmptyState
          v-else-if="error && !team"
          compact
          tone="error"
          :title="t('credits.panel.loadFailed')"
          :desc="error"
          :action="t('credits.panel.retry')"
          @action="emit('retry')"
        />

        <template v-else-if="team">
          <div class="actp__plan">
            <v-select
              autocomplete="off"
              :model-value="team.plan.key"
              :items="planOptions"
              item-title="title"
              item-value="value"
              variant="outlined"
              density="comfortable"
              :label="t('credits.panel.plan')"
              :loading="planSaving"
              :disabled="planSaving"
              hide-details
              @update:model-value="emit('change-plan', $event)"
            />
            <AdminFlash
              v-if="planError"
              tone="error"
              :text="planError"
              :dismiss-aria="t('credits.panel.close')"
              @dismiss="emit('dismiss-plan-error')"
            />
          </div>

          <section class="actp__section">
            <div class="actp__sectionhead">
              <h3 class="actp__h t-title">{{ t('credits.panel.credits') }}</h3>
              <BaseButton kind="secondary" size="sm" @click="emit('grant')">{{ t('credits.panel.grant') }}</BaseButton>
            </div>
            <ul v-if="team.packs.length" class="actp__packs">
              <li v-for="pack in team.packs" :key="pack.id" class="actp__pack">
                <AdminMeterBar
                  :label="packName(pack)"
                  :value-text="`${fmtCredits(pack.credits_used, locale)} / ${fmtCredits(pack.credits_total, locale)}`"
                  :limit="pack.credits_total"
                  :ratio="ratio(pack)"
                  :tone="meterTone(ratio(pack))"
                />
                <span class="actp__packmeta t-meta-read">{{ packMeta(pack) }}</span>
              </li>
            </ul>
            <p v-else class="actp__none t-meta-read">{{ t('credits.panel.emptyPacks') }}</p>
          </section>

          <section class="actp__section">
            <h3 class="actp__h t-title">{{ t('credits.panel.history') }}</h3>
            <p v-if="historyError" class="actp__error t-body" role="alert">
              {{ t('credits.panel.historyFailed') }}
            </p>
            <ul v-else-if="history && history.length" class="actp__log">
              <li v-for="(entry, i) in history" :key="`${entry.created_at}-${i}`" class="actp__logitem">
                <span class="actp__logwhat t-body">{{ historyLine(entry) }}</span>
                <span class="t-meta-read actp__logmeta">
                  {{
                    t('credits.panel.by', { handle: entry.actor_handle, time: fmtDateTime(entry.created_at, locale) })
                  }}
                </span>
              </li>
            </ul>
            <p v-else-if="history" class="actp__none t-meta-read">{{ t('credits.panel.emptyHistory') }}</p>
          </section>
        </template>
      </div>
    </div>
  </v-navigation-drawer>
</template>

<style scoped>
.actp {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.actp__head {
  display: flex;
  flex: 0 0 auto;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 16px 12px 24px;
  border-bottom: 1px solid var(--line);
}

.actp__id {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.actp__title {
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.actp__meta {
  color: var(--muted);
}

.actp__body {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 24px;
  min-height: 0;
  padding: 20px 24px 24px;
  overflow-y: auto;
}

.actp__plan {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 8px;
}

.actp__section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.actp__sectionhead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.actp__h {
  margin: 0;
  color: var(--ink);
}

.actp__packs,
.actp__log {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
}

.actp__packs {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.actp__pack {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 16px;
}

.actp__pack + .actp__pack {
  border-top: 1px solid var(--line);
}

.actp__packmeta,
.actp__logmeta {
  color: var(--muted);
}

.actp__log {
  gap: 12px;
}

.actp__logitem {
  display: flex;
  flex-direction: column;
}

.actp__logwhat {
  color: var(--text);
}

.actp__none {
  margin: 0;
  color: var(--muted);
}

.actp__error {
  margin: 0;
  color: var(--danger-ink);
}
</style>
