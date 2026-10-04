<script setup lang="ts">
import type { ModelTier, Plan, PlanAudience, PlanInput, PlanWindow } from '@/lib/adminCredits'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { AUDIENCE_KEY, MODEL_TIERS, planTiers, TIER_KEY } from '@/lib/adminCredits'

// 新建或编辑一个方案：名称、适用对象、排序、计费方式（按月发放，或按时间窗口限额，
// 二选一）、可用的模型档位。编辑的改动从下一期起生效（本期已发的额度不变）。
// 没有团队在用的方案可以在这里删除，新团队默认的方案不能删。
const props = withDefaults(
  defineProps<{
    modelValue: boolean
    /** 要编辑的方案；`null` = 新建。 */
    plan?: Plan | null
    saving?: boolean
    error?: string | null
    /** 每一档现在有哪些模型（名字），用来在勾选框旁说明这一档是什么。 */
    tierModels?: Partial<Record<ModelTier, string[]>>
  }>(),
  { plan: null, saving: false, error: null, tierModels: () => ({}) }
)

const emit = defineEmits<{
  'update:modelValue': [open: boolean]
  submit: [input: PlanInput]
  delete: []
}>()

const { t, locale } = useI18n()

type Billing = 'monthly' | 'windows'
/** 窗口的长度：几小时（从第一次使用起算），或每周、每月（按日历清零）。 */
type WindowSpan = 'hours' | 'week' | 'month'

interface WindowDraft {
  span: WindowSpan
  hours: string
  credits: string
}

const name = ref('')
const audience = ref<PlanAudience>('both')
const rank = ref('0')
const billing = ref<Billing>('monthly')
const credits = ref('')
const windows = ref<WindowDraft[]>([])
const tiers = ref<ModelTier[]>(['included'])
/** 正在确认删除：「删除方案」就地换成「确认删除 / 不删了」。 */
const confirmingDelete = ref(false)

function draftOf(w: PlanWindow): WindowDraft {
  return {
    span: w.calendar ?? 'hours',
    hours: w.hours === undefined ? '' : String(w.hours),
    credits: String(w.credits),
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    const plan = props.plan
    name.value = plan?.name ?? ''
    audience.value = plan?.audience ?? 'both'
    rank.value = String(plan?.rank ?? 0)
    billing.value = plan?.windows.length ? 'windows' : 'monthly'
    credits.value = plan?.credits_per_period == null ? '' : String(plan.credits_per_period)
    windows.value = (plan?.windows ?? []).map(draftOf)
    if (billing.value === 'windows' && !windows.value.length) addWindow()
    tiers.value = plan ? planTiers(plan) : ['included']
    confirmingDelete.value = false
  },
  { immediate: true }
)

const editing = computed(() => props.plan !== null)
const unlimited = computed(() => !!props.plan?.unlimited)

/** 删不了的原因；能删为 `null`。 */
const undeletable = computed(() => {
  const plan = props.plan
  if (!plan) return null
  if (plan.is_default) return t('credits.planDialog.deleteDefault')
  if (plan.team_count > 0) return t('credits.planDialog.deleteInUse', { n: plan.team_count })
  return null
})

const audienceOptions = computed(() =>
  (['both', 'personal', 'team'] as PlanAudience[]).map((value) => ({ value, title: t(AUDIENCE_KEY[value]) }))
)

const spanOptions = computed(() => [
  { value: 'hours', title: t('credits.planDialog.spanHours') },
  { value: 'week', title: t('credits.planDialog.spanWeek') },
  { value: 'month', title: t('credits.planDialog.spanMonth') },
])

function numberOrNull(raw: string): number | null {
  const text = raw.trim()
  if (text === '') return null
  const n = Number(text)
  return Number.isFinite(n) ? n : Number.NaN
}

function nonNegative(n: number | null): n is number {
  return n !== null && !Number.isNaN(n) && n >= 0
}

const creditsValue = computed(() => numberOrNull(credits.value))
const rankValue = computed(() => numberOrNull(rank.value))
const windowValues = computed<(PlanWindow | null)[]>(() =>
  windows.value.map((w) => {
    const cap = numberOrNull(w.credits)
    if (!nonNegative(cap) || cap === 0) return null
    if (w.span !== 'hours') return { calendar: w.span, credits: cap }
    const hours = numberOrNull(w.hours)
    return hours !== null && !Number.isNaN(hours) && hours > 0 ? { hours, credits: cap } : null
  })
)

/** 同样长度的窗口只能有一个：两条「每周」说的是同一件事。 */
const duplicateWindow = computed(() => {
  const keys = windowValues.value.map((w) => (w ? w.calendar ?? `${w.hours}h` : null)).filter(Boolean)
  return new Set(keys).size !== keys.length
})

const invalid = computed(() => {
  if (!name.value.trim()) return true
  const r = rankValue.value
  if (r === null || !Number.isInteger(r)) return true
  if (unlimited.value) return false
  if (billing.value === 'monthly') return !nonNegative(creditsValue.value)
  return !windowValues.value.length || windowValues.value.some((w) => w === null) || duplicateWindow.value
})

function tierHint(tier: ModelTier): string {
  const names = props.tierModels[tier]
  if (!names) return ''
  if (!names.length) return t('credits.planDialog.tierModelsNone')
  const sep = locale.value === 'en' ? ', ' : '、'
  if (names.length <= 2) return t('credits.planDialog.tierModelsAll', { names: names.join(sep) })
  return t('credits.planDialog.tierModels', { names: names.slice(0, 2).join(sep), n: names.length })
}

function addWindow() {
  windows.value = [...windows.value, { span: 'hours', hours: '', credits: '' }]
}

function removeWindow(index: number) {
  windows.value = windows.value.filter((_, i) => i !== index)
}

function setBilling(value: Billing) {
  billing.value = value
  if (value === 'windows' && !windows.value.length) addWindow()
}

function close() {
  if (!props.saving) emit('update:modelValue', false)
}

function submit() {
  if (invalid.value) return
  const input: PlanInput = {
    name: name.value.trim(),
    audience: audience.value,
    model_tiers: MODEL_TIERS.filter((tier) => tiers.value.includes(tier)),
    rank: rankValue.value as number,
  }
  if (!unlimited.value) {
    const monthly = billing.value === 'monthly'
    input.credits_per_period = monthly ? creditsValue.value : null
    input.windows = monthly ? [] : (windowValues.value as PlanWindow[])
  }
  emit('submit', input)
}
</script>

<template>
  <AdaptiveDialog
    :model-value="modelValue"
    :title="editing ? t('credits.planDialog.editTitle') : t('credits.planDialog.createTitle')"
    :primary-label="editing ? t('credits.planDialog.save') : t('credits.planDialog.create')"
    :primary-loading="saving"
    :primary-disabled="invalid || saving"
    :close-disabled="saving"
    @update:model-value="!$event && close()"
    @primary="submit"
  >
    <div class="apd__row">
      <v-text-field
        v-model="name"
        autocomplete="off"
        variant="outlined"
        density="comfortable"
        :label="t('credits.planDialog.name')"
        hide-details
      />
      <v-select
        v-model="audience"
        autocomplete="off"
        :items="audienceOptions"
        item-title="title"
        item-value="value"
        variant="outlined"
        density="comfortable"
        :label="t('credits.planDialog.audience')"
        hide-details
      />
    </div>

    <v-text-field
      v-model="rank"
      autocomplete="off"
      type="number"
      step="1"
      variant="outlined"
      density="comfortable"
      :label="t('credits.planDialog.rank')"
      :hint="t('credits.planDialog.rankHint')"
      persistent-hint
      class="apd__rank"
    />

    <div class="apd__group">
      <span class="apd__legend">{{ t('credits.planDialog.billing') }}</span>
      <div class="apd__box">
        <div v-if="unlimited" class="apd__line">
          <span class="apd__static">{{ t('credits.unlimited') }}</span>
        </div>
        <template v-else>
          <div class="apd__line">
            <v-btn-toggle
              :model-value="billing"
              mandatory
              density="compact"
              variant="outlined"
              divided
              @update:model-value="setBilling"
            >
              <!-- eslint-disable-next-line vue/no-restricted-syntax -- a segment of v-btn-toggle, not one of the BaseButton roles -->
              <v-btn value="monthly" size="small">{{ t('credits.planDialog.billingMonthly') }}</v-btn>
              <!-- eslint-disable-next-line vue/no-restricted-syntax -- a segment of v-btn-toggle, not one of the BaseButton roles -->
              <v-btn value="windows" size="small">{{ t('credits.planDialog.billingWindows') }}</v-btn>
            </v-btn-toggle>
          </div>
          <div v-if="billing === 'monthly'" class="apd__line">
            <span class="apd__lineLabel">{{ t('credits.planDialog.perPeriod') }}</span>
            <v-text-field
              v-model="credits"
              autocomplete="off"
              type="number"
              min="0"
              variant="outlined"
              density="compact"
              :aria-label="t('credits.planDialog.perPeriod')"
              :suffix="t('credits.planDialog.unit')"
              hide-details
              class="apd__num"
            />
          </div>
          <template v-else>
            <div v-for="(w, i) in windows" :key="i" class="apd__line apd__line--window">
              <v-select
                v-model="w.span"
                autocomplete="off"
                :items="spanOptions"
                item-title="title"
                item-value="value"
                variant="outlined"
                density="compact"
                :aria-label="t('credits.planDialog.windowSpan')"
                hide-details
                class="apd__span"
              />
              <v-text-field
                v-if="w.span === 'hours'"
                v-model="w.hours"
                autocomplete="off"
                type="number"
                min="0"
                variant="outlined"
                density="compact"
                :aria-label="t('credits.planDialog.windowHours')"
                :suffix="t('credits.planDialog.hoursUnit')"
                hide-details
                class="apd__num"
              />
              <v-text-field
                v-model="w.credits"
                autocomplete="off"
                type="number"
                min="0"
                variant="outlined"
                density="compact"
                :label="t('credits.planDialog.windowCredits')"
                :suffix="t('credits.planDialog.unit')"
                hide-details
                class="apd__num"
              />
              <BaseButton
                icon="mdi-close"
                size="sm"
                :disabled="windows.length === 1"
                :aria-label="t('credits.planDialog.removeWindow')"
                @click="removeWindow(i)"
              />
            </div>
            <div class="apd__line">
              <BaseButton kind="ghost" size="sm" prepend-icon="mdi-plus" @click="addWindow">
                {{ t('credits.planDialog.addWindow') }}
              </BaseButton>
              <span v-if="duplicateWindow" class="apd__warn t-meta-read">
                {{ t('credits.planDialog.duplicateWindow') }}
              </span>
            </div>
          </template>
        </template>
      </div>
      <span v-if="!unlimited && billing === 'windows'" class="apd__note t-meta-read">
        {{ t('credits.planDialog.windowsHint') }}
      </span>
    </div>

    <fieldset class="apd__group apd__tiers">
      <legend class="apd__legend">{{ t('credits.planDialog.tiers') }}</legend>
      <v-checkbox v-for="tier in MODEL_TIERS" :key="tier" v-model="tiers" :value="tier" density="compact" hide-details>
        <template #label>
          <span class="apd__tier">{{ t(TIER_KEY[tier]) }}</span>
          <span v-if="tierHint(tier)" class="apd__hint t-meta-read">{{ tierHint(tier) }}</span>
        </template>
      </v-checkbox>
    </fieldset>

    <p v-if="editing" class="apd__note apd__nextPeriod t-meta-read">{{ t('credits.planDialog.nextPeriod') }}</p>

    <v-alert v-if="error" type="error" density="compact" variant="tonal" class="mt-3" role="alert">
      {{ error }}
    </v-alert>
    <template #actions>
      <template v-if="editing">
        <span v-if="undeletable" class="apd__note t-meta-read">{{ undeletable }}</span>
        <template v-else-if="confirmingDelete">
          <BaseButton kind="danger" solid :loading="saving" :disabled="saving" @click="emit('delete')">
            {{ t('credits.planDialog.confirmDelete') }}
          </BaseButton>
          <BaseButton kind="ghost" :disabled="saving" @click="confirmingDelete = false">
            {{ t('credits.planDialog.keep') }}
          </BaseButton>
        </template>
        <BaseButton v-else kind="ghost" :disabled="saving" @click="confirmingDelete = true">
          {{ t('credits.planDialog.delete') }}
        </BaseButton>
      </template>
    </template>
  </AdaptiveDialog>
</template>

<style scoped>
.apd__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
  padding-top: 8px;
}

.apd__group {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 20px 0 0;
  padding: 0;
  border: 0;
}

.apd__legend {
  padding: 0;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}

.apd__box {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.apd__line {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
}

.apd__line + .apd__line {
  border-top: 1px solid var(--line);
}

.apd__lineLabel {
  flex: 0 0 120px;
  color: var(--text);
}

.apd__static {
  color: var(--ink);
}

.apd__num {
  flex: 0 1 160px;
}

.apd__span {
  flex: 0 1 120px;
}

.apd__rank {
  margin-top: 16px;
}

.apd__warn {
  color: var(--danger-ink);
}

.apd__nextPeriod {
  margin: 12px 0 0;
}

.apd__tier {
  color: var(--ink);
}

.apd__hint {
  margin-left: 8px;
  color: var(--muted);
}

.apd__note {
  color: var(--muted);
}

@media (max-width: 700px) {
  .apd__row {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
