<script setup lang="ts">
import type { ModelTier, Plan, PlanAudience, PlanInput } from '@/lib/adminCredits'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { AUDIENCE_KEY, MODEL_TIERS, planTiers, TIER_KEY } from '@/lib/adminCredits'

// 新建或编辑一个方案：名称、适用对象、每月发放与使用上限、可用的模型档位。
// 编辑的改动从下一期起生效（本期已发的额度不变）。
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
}>()

const { t, locale } = useI18n()

interface WindowDraft {
  hours: string
  credits: string
}

const name = ref('')
const audience = ref<PlanAudience>('both')
const credits = ref('')
const windows = ref<WindowDraft[]>([])
const tiers = ref<ModelTier[]>(['included'])

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    const plan = props.plan
    name.value = plan?.name ?? ''
    audience.value = plan?.audience ?? 'both'
    credits.value = plan?.credits_per_period === null || plan === null ? '' : String(plan.credits_per_period)
    windows.value = (plan?.windows ?? []).map((w) => ({ hours: String(w.hours), credits: String(w.credits) }))
    tiers.value = plan ? planTiers(plan) : ['included']
  },
  { immediate: true }
)

const editing = computed(() => props.plan !== null)
const unlimited = computed(() => !!props.plan?.unlimited)

const audienceOptions = computed(() =>
  (['both', 'personal', 'team'] as PlanAudience[]).map((value) => ({ value, title: t(AUDIENCE_KEY[value]) }))
)

function numberOrNull(raw: string): number | null {
  const text = raw.trim()
  if (text === '') return null
  const n = Number(text)
  return Number.isFinite(n) ? n : Number.NaN
}

const creditsValue = computed(() => numberOrNull(credits.value))
const windowValues = computed(() =>
  windows.value.map((w) => ({ hours: numberOrNull(w.hours), credits: numberOrNull(w.credits) }))
)

const invalid = computed(() => {
  if (!name.value.trim()) return true
  if (!unlimited.value) {
    const c = creditsValue.value
    if (c === null || Number.isNaN(c) || c < 0) return true
  }
  return windowValues.value.some(
    (w) =>
      w.hours === null ||
      Number.isNaN(w.hours) ||
      w.hours <= 0 ||
      w.credits === null ||
      Number.isNaN(w.credits) ||
      w.credits < 0
  )
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
  windows.value = [...windows.value, { hours: '', credits: '' }]
}

function removeWindow(index: number) {
  windows.value = windows.value.filter((_, i) => i !== index)
}

function close() {
  if (!props.saving) emit('update:modelValue', false)
}

function submit() {
  if (invalid.value) return
  const input: PlanInput = {
    name: name.value.trim(),
    audience: audience.value,
    windows: windowValues.value.map((w) => ({ hours: w.hours as number, credits: w.credits as number })),
    model_tiers: MODEL_TIERS.filter((tier) => tiers.value.includes(tier)),
  }
  if (!unlimited.value) input.credits_per_period = creditsValue.value
  emit('submit', input)
}
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="560" :persistent="saving" @update:model-value="!$event && close()">
    <v-card rounded="lg">
      <v-card-title class="t-dialog-title px-4 pt-4 pb-2">
        {{ editing ? t('credits.planDialog.editTitle') : t('credits.planDialog.createTitle') }}
      </v-card-title>

      <v-card-text class="px-4">
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

        <div class="apd__group">
          <span class="apd__legend">{{ t('credits.planDialog.credits') }}</span>
          <div class="apd__box">
            <div class="apd__line">
              <span class="apd__lineLabel">{{ t('credits.planDialog.perPeriod') }}</span>
              <span v-if="unlimited" class="apd__static">{{ t('credits.unlimited') }}</span>
              <v-text-field
                v-else
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
            <div v-for="(w, i) in windows" :key="i" class="apd__line apd__line--window">
              <v-text-field
                v-model="w.hours"
                autocomplete="off"
                type="number"
                min="0"
                variant="outlined"
                density="compact"
                :label="t('credits.planDialog.windowHours')"
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
              <v-btn
                icon="mdi-close"
                variant="text"
                size="small"
                :aria-label="t('credits.planDialog.removeWindow')"
                @click="removeWindow(i)"
              />
            </div>
            <div class="apd__line">
              <v-btn variant="text" size="small" prepend-icon="mdi-plus" @click="addWindow">
                {{ t('credits.planDialog.addWindow') }}
              </v-btn>
            </div>
          </div>
        </div>

        <fieldset class="apd__group apd__tiers">
          <legend class="apd__legend">{{ t('credits.planDialog.tiers') }}</legend>
          <v-checkbox
            v-for="tier in MODEL_TIERS"
            :key="tier"
            v-model="tiers"
            :value="tier"
            density="compact"
            hide-details
          >
            <template #label>
              <span class="apd__tier">{{ t(TIER_KEY[tier]) }}</span>
              <span v-if="tierHint(tier)" class="apd__hint t-meta-read">{{ tierHint(tier) }}</span>
            </template>
          </v-checkbox>
        </fieldset>

        <v-alert v-if="error" type="error" density="compact" variant="tonal" class="mt-3" role="alert">
          {{ error }}
        </v-alert>
      </v-card-text>

      <v-card-actions class="pa-4 pt-0">
        <span v-if="editing" class="apd__note t-meta-read">{{ t('credits.planDialog.nextPeriod') }}</span>
        <v-spacer />
        <v-btn variant="text" :disabled="saving" @click="close">{{ t('credits.planDialog.cancel') }}</v-btn>
        <v-btn color="primary" variant="flat" :loading="saving" :disabled="invalid || saving" @click="submit">
          {{ editing ? t('credits.planDialog.save') : t('credits.planDialog.create') }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
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
