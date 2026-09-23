<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { fmtCost } from '@/lib/usageFormat'

// 给一个项目的网关 key 设「刹车值」（`max_budget`，契约 §3.4）。
//
// 这个值的语义只有一句要讲清楚：**它是网关账本上的硬闸，不是项目自己的额度**。
// 项目额度（credits）在平台库里，按它乘单价推导出一个建议刹车值；这里设的是一个
// **覆盖值**，一旦设了就压过推导值。所以框里要说得出「现在生效的是哪一个」，
// 否则管理员会以为清空就是把额度清零。
//
// 空框 = 清空覆盖，回落到按额度推导的那个值。这个「空 = 清除」是这一层唯一的约定，
// 所以「清除」另给一个明确的按钮 —— 只靠把框清空来表达清空，人要自己猜。

/** 这一层要用到的项目行字段（§3.4）。 */
interface BudgetSeed {
  project_id: string
  name: string
  key_alias: string
  max_budget_usd: number | null
  budget_derived_usd: number | null
  budget_override_usd: number | null
  credits: { total: number | null; used: number; remaining: number; unlimited: boolean }
}

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    project?: BudgetSeed | null
    saving?: boolean
    error?: string | null
  }>(),
  { project: null, saving: false, error: null }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'submit', maxBudgetUsd: number | null): void
}>()

const { t } = useI18n()

const text = ref('')

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    const v = props.project?.max_budget_usd
    text.value = v === null || v === undefined ? '' : String(v)
  },
  { immediate: true }
)

const parsed = computed(() => {
  const raw = text.value.trim()
  if (raw === '') return null
  const n = Number(raw)
  return Number.isFinite(n) && n > 0 ? n : NaN
})

const invalid = computed(() => Number.isNaN(parsed.value))

/** 回落到推导值时，那个值是多少 —— 空框保存之后生效的就是它。`unlimited` 时没有推导值。 */
const derivedText = computed(() => {
  const p = props.project
  if (!p) return '—'
  if (p.credits.unlimited) return t('models.budget.unlimited')
  if (p.budget_derived_usd === null || p.budget_derived_usd === undefined) return '—'
  return fmtCost(p.budget_derived_usd)
})

function close() {
  emit('update:modelValue', false)
}

function submit() {
  if (invalid.value || props.saving) return
  emit('submit', parsed.value)
}
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="480" :persistent="saving" @update:model-value="!$event && close()">
    <v-card rounded="lg">
      <v-card-title class="px-4 pt-4 pb-2">{{ t('models.budget.dialog.title') }}</v-card-title>

      <v-card-text class="px-4">
        <p class="amb__who t-body">{{ project?.name }}</p>
        <p class="amb__meta t-meta-read">{{ project?.key_alias }}</p>

        <v-text-field
          v-model="text"
          autocomplete="off"
          type="number"
          variant="outlined"
          density="comfortable"
          :label="t('models.budget.dialog.field')"
          :hint="t('models.budget.dialog.hint', { derived: derivedText })"
          :error="invalid"
          hide-details="auto"
          class="mt-3"
        />

        <v-alert v-if="error" type="error" density="compact" variant="tonal" class="amb__alert" role="alert">
          {{ error }}
        </v-alert>
      </v-card-text>

      <v-card-actions class="pa-4 pt-0">
        <!-- 清除只在一个值确实存在时才有意义（没有覆盖值时它是个空操作）。 -->
        <v-btn
          v-if="project?.max_budget_usd !== null && project?.max_budget_usd !== undefined"
          variant="text"
          :disabled="saving"
          @click="emit('submit', null)"
        >
          {{ t('models.budget.dialog.clear') }}
        </v-btn>
        <v-spacer />
        <v-btn variant="text" :disabled="saving" @click="close">{{ t('models.dialog.cancel') }}</v-btn>
        <v-btn color="primary" :loading="saving" :disabled="invalid || saving" @click="submit">
          {{ t('models.dialog.save') }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.amb__who {
  margin: 0;
}

.amb__meta {
  margin: 0;
  color: var(--faint);
}

.amb__alert {
  margin-top: 12px;
}
</style>
