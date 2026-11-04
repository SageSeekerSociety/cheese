<script setup lang="ts">
import type { CreditBudgetUpdate, ProjectCreditBudget } from '@/types/creditBudgets'

import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import CreditWarning from '@/components/usage/CreditWarning.vue'
import { t } from '@/i18n'

const props = defineProps<{
  budget: ProjectCreditBudget | null
  editable: boolean
  loading?: boolean
  saving?: boolean
  error?: string | null
}>()
const emit = defineEmits<{ save: [update: CreditBudgetUpdate]; reload: [] }>()
const total = ref('')
const members = ref<Record<string, string>>({})

watch(
  () => props.budget,
  (budget) => {
    total.value = budget?.credits_limit?.toString() ?? ''
    members.value = Object.fromEntries(
      Object.entries(budget?.members ?? {}).map(([handle, member]) => [handle, member.credits_limit?.toString() ?? ''])
    )
  },
  { immediate: true }
)

function limit(value: string): number | null {
  return value.trim() === '' ? null : Number(value)
}

const valid = computed(() =>
  [total.value, ...Object.values(members.value)].every((value) => {
    const n = limit(value)
    return n === null || (Number.isFinite(n) && n >= 0)
  })
)

const update = computed<CreditBudgetUpdate>(() => {
  const patch: CreditBudgetUpdate = {}
  if (!props.budget) return patch
  if (limit(total.value) !== props.budget.credits_limit) patch.credits_limit = limit(total.value)
  for (const [handle, value] of Object.entries(members.value)) {
    if (limit(value) !== props.budget.members[handle]?.credits_limit) {
      patch.member_limits ??= {}
      patch.member_limits[handle] = limit(value)
    }
  }
  return patch
})
const dirty = computed(() => Object.keys(update.value).length > 0)
function fmt(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

// 这个项目的预算用到八成时，在预算这一块顶上提醒一句。和 `usage.alerts` 同一档、
// 同一句话的两个来源：这里读的是同一个接口给的数。
const warnings = computed(() => {
  const budget = props.budget
  if (!budget) return []
  const out: { title: string; body: string }[] = []
  const limit = budget.credits_limit
  if (limit !== null && limit > 0 && budget.credits_used >= limit * 0.8 && budget.credits_used < limit) {
    out.push({
      title: t('usage.warning.projectTitle'),
      body: t('usage.warning.projectBody', { n: fmt(limit - budget.credits_used) }),
    })
  }
  return out
})

function save() {
  if (props.editable && valid.value && dirty.value && !props.saving) emit('save', update.value)
}
</script>

<template>
  <section class="page-section credit-budget">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-gauge</v-icon>
      <span class="page-section-title">{{ t('work.projectSettings.credits.budgetTitle') }}</span>
    </div>
    <div class="page-section-body">
      <v-alert v-if="error" type="error" density="compact" class="mb-3">
        {{ error }}
        <BaseButton v-if="!budget" size="sm" @click="emit('reload')">{{
          t('work.projectSettings.credits.retry')
        }}</BaseButton>
      </v-alert>
      <p v-if="loading" class="t-body c-muted">{{ t('work.projectSettings.credits.loading') }}</p>
      <form v-else-if="budget" @submit.prevent="save">
        <CreditWarning v-for="w in warnings" :key="w.title" :title="w.title" :body="w.body" class="mb-4" />
        <p class="t-meta c-muted mb-4">{{ t('work.projectSettings.credits.budgetHint') }}</p>
        <div class="credit-budget__total">
          <v-text-field
            v-if="editable"
            v-model="total"
            :label="t('work.projectSettings.credits.projectLimit')"
            :placeholder="t('work.projectSettings.credits.noSeparateLimit')"
            type="number"
            min="0"
            step="any"
            variant="outlined"
            density="compact"
            :disabled="saving"
            hide-details
          />
          <p v-else class="t-body">
            {{ t('work.projectSettings.credits.projectLimit') }}：{{
              budget.credits_limit === null
                ? t('work.projectSettings.credits.noSeparateLimit')
                : fmt(budget.credits_limit)
            }}
          </p>
          <p class="t-meta c-muted">
            {{
              t('work.projectSettings.credits.projectSpent', {
                used: fmt(budget.credits_used),
                public: fmt(budget.public_credits),
              })
            }}
          </p>
        </div>
        <p v-if="budget.unattributed_credits > 0" class="t-meta c-muted mt-3">
          {{ t('work.projectSettings.credits.unattributed', { credits: fmt(budget.unattributed_credits) }) }}
        </p>
        <div class="credit-budget__members">
          <table>
            <thead>
              <tr>
                <th scope="col">{{ t('work.projectSettings.credits.member') }}</th>
                <th scope="col">{{ t('work.projectSettings.credits.taskSpent') }}</th>
                <th scope="col">{{ t('work.projectSettings.credits.memberLimit') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(member, handle) in budget.members" :key="handle">
                <th scope="row">{{ member.name || handle }}</th>
                <td class="ln-num">{{ fmt(member.credits_used) }}</td>
                <td>
                  <v-text-field
                    v-if="editable"
                    v-model="members[handle]"
                    :aria-label="t('work.projectSettings.credits.memberLimitFor', { name: member.name || handle })"
                    :placeholder="t('work.projectSettings.credits.noSeparateLimit')"
                    type="number"
                    min="0"
                    step="any"
                    variant="outlined"
                    density="compact"
                    :disabled="saving"
                    hide-details
                  />
                  <span v-else>{{
                    member.credits_limit === null
                      ? t('work.projectSettings.credits.noSeparateLimit')
                      : fmt(member.credits_limit)
                  }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="!valid" role="alert" class="t-meta c-danger mt-2">
          {{ t('work.projectSettings.credits.validLimit') }}
        </p>
        <BaseButton
          v-if="editable"
          type="submit"
          kind="primary"
          class="mt-4"
          :loading="saving"
          :disabled="!valid || !dirty || saving"
        >
          {{ t('work.projectSettings.credits.saveBudget') }}
        </BaseButton>
      </form>
    </div>
  </section>
</template>

<style scoped>
.credit-budget__total {
  display: grid;
  gap: 10px;
}
.credit-budget__members {
  overflow-x: auto;
  margin-top: 20px;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th,
td {
  padding: 10px 8px;
  text-align: left;
  border-bottom: 1px solid var(--line);
}
thead th {
  font-size: 12px;
  font-weight: 500;
  color: var(--muted);
}
tbody th {
  font-size: 13px;
  font-weight: 500;
}
td:last-child {
  width: 180px;
  min-width: 150px;
}
</style>
