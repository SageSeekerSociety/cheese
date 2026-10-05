<script setup lang="ts">
import type { Plan, PlanWindow } from '@/lib/adminCredits'

import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import BaseTable from '@/components/base/BaseTable.vue'
import { AUDIENCE_KEY, fmtCredits, planTiers, TIER_KEY } from '@/lib/adminCredits'

// 方案一览：每个方案发多少、限多少、能用哪几档模型。
const props = defineProps<{
  /** `null` = 还没到货（或读失败，见 `error`）。 */
  plans: Plan[] | null
  loading: boolean
  /** 读失败的原话；`null` = 没失败。 */
  error: string | null
}>()

const emit = defineEmits<{ edit: [plan: Plan]; retry: [] }>()

const { t, locale } = useI18n()

function creditsText(plan: Plan): string {
  if (plan.unlimited) return t('credits.unlimited')
  return plan.credits_per_period === null ? '—' : fmtCredits(plan.credits_per_period, locale.value)
}

function windowText(w: PlanWindow): string {
  const credits = fmtCredits(w.credits, locale.value)
  if (w.calendar === 'week') return t('credits.plans.windowWeek', { credits })
  if (w.calendar === 'month') return t('credits.plans.windowMonth', { credits })
  return t('credits.plans.window', { hours: fmtCredits(w.hours ?? 0, locale.value), credits })
}

function windowsText(plan: Plan): string {
  if (!plan.windows.length) return t('credits.plans.noWindows')
  return plan.windows.map(windowText).join(locale.value === 'en' ? '; ' : '；')
}

function tiersText(plan: Plan): string {
  return planTiers(plan)
    .map((tier) => t(TIER_KEY[tier]))
    .join(locale.value === 'en' ? ', ' : '、')
}
</script>

<template>
  <BaseTable
    class="acp"
    :label="t('credits.plans.label')"
    :cols="[null, '120px', '120px', '200px', '220px', '88px', '88px']"
    :loading="props.loading && !props.plans"
    :skeleton-rows="2"
    :state="props.error !== null ? 'error' : 'rows'"
    cards
    :empty="props.plans && !props.plans.length ? t('credits.plans.empty') : null"
  >
    <template #head>
      <tr>
        <th scope="col">{{ t('credits.plans.column.name') }}</th>
        <th scope="col">{{ t('credits.plans.column.audience') }}</th>
        <th scope="col">{{ t('credits.plans.column.credits') }}</th>
        <th scope="col">{{ t('credits.plans.column.windows') }}</th>
        <th scope="col">{{ t('credits.plans.column.tiers') }}</th>
        <th scope="col" class="acp__num">{{ t('credits.plans.column.teams') }}</th>
        <th scope="col" class="acp__num">{{ t('credits.plans.column.actions') }}</th>
      </tr>
    </template>

    <template #error>
      <BaseLoadError
        :title="t('credits.plans.loadFailed')"
        :error="props.error || undefined"
        :retry-label="t('credits.plans.retry')"
        @retry="emit('retry')"
      />
    </template>

    <tr v-for="plan in props.plans ?? []" :key="plan.key">
      <td data-card="primary">
        <span class="acp__name">
          <span class="acp__title">{{ plan.name }}</span>
          <span v-if="plan.is_default" class="acp__tag">{{ t('credits.plans.tagDefault') }}</span>
          <span v-if="plan.admin_only" class="acp__tag">{{ t('credits.plans.tagAdminOnly') }}</span>
        </span>
      </td>
      <td :data-label="t('credits.plans.column.audience')">{{ t(AUDIENCE_KEY[plan.audience]) }}</td>
      <td class="t-num" :data-label="t('credits.plans.column.credits')">{{ creditsText(plan) }}</td>
      <td :data-label="t('credits.plans.column.windows')">{{ windowsText(plan) }}</td>
      <td :data-label="t('credits.plans.column.tiers')">{{ tiersText(plan) }}</td>
      <td class="acp__num t-num" :data-label="t('credits.plans.column.teams')">
        {{ fmtCredits(plan.team_count, locale) }}
      </td>
      <td class="acp__num" :data-label="t('credits.plans.column.actions')">
        <BaseButton kind="ghost" size="sm" @click="emit('edit', plan)">{{ t('credits.plans.edit') }}</BaseButton>
      </td>
    </tr>
  </BaseTable>
</template>

<style scoped>
/* 六列在内容区里放得下：不要表壳那条为宽表准备的横滚下限。窄屏走卡片模式。 */
.acp :deep(.agrid__table) {
  min-width: 760px;
}

.acp__name {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.acp__title {
  color: var(--ink);
  font-weight: 600;
}

.acp__tag {
  padding: 0 6px;
  background: var(--fill-2);
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.acp__num {
  text-align: right;
}
</style>
