<script setup lang="ts">
import type { GrantInput } from '@/lib/adminCredits'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { endOfDayIso } from '@/lib/adminCredits'

// 给一个团队发额度：数量、到期时间（不过期或某一天）、原因（只有管理员看得到）。
const props = withDefaults(
  defineProps<{
    modelValue: boolean
    /** 发给谁：标题里写它的名字。 */
    teamName: string
    saving?: boolean
    error?: string | null
  }>(),
  { saving: false, error: null }
)

const emit = defineEmits<{
  'update:modelValue': [open: boolean]
  submit: [input: GrantInput]
}>()

const { t } = useI18n()

const amount = ref('')
const expiry = ref<'never' | 'date'>('never')
const date = ref('')
const reason = ref('')

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    amount.value = ''
    expiry.value = 'never'
    date.value = ''
    reason.value = ''
  },
  { immediate: true }
)

const credits = computed(() => {
  const n = Number(amount.value.trim())
  return amount.value.trim() !== '' && Number.isFinite(n) ? n : null
})

const invalid = computed(() => credits.value === null || credits.value <= 0 || (expiry.value === 'date' && !date.value))

function close() {
  if (!props.saving) emit('update:modelValue', false)
}

function submit() {
  if (invalid.value || credits.value === null) return
  const note = reason.value.trim()
  emit('submit', {
    credits: credits.value,
    expires_at: expiry.value === 'date' ? endOfDayIso(date.value) : null,
    reason: note || null,
  })
}
</script>

<template>
  <AdaptiveDialog
    :model-value="modelValue"
    :title="t('credits.grantDialog.title', { team: teamName })"
    :primary-label="t('credits.grantDialog.submit')"
    :cancel-label="t('credits.grantDialog.cancel')"
    :primary-loading="saving"
    :primary-disabled="invalid || saving"
    :close-disabled="saving"
    @update:model-value="close"
    @primary="submit"
  >
    <v-text-field
      v-model="amount"
      autocomplete="off"
      type="number"
      min="0"
      variant="outlined"
      density="comfortable"
      :label="t('credits.grantDialog.amount')"
      hide-details
      class="agd__amount"
    />

    <fieldset class="agd__group">
      <legend class="agd__legend">{{ t('credits.grantDialog.expiry') }}</legend>
      <v-radio-group v-model="expiry" hide-details density="compact">
        <v-radio value="never" :label="t('credits.grantDialog.never')" />
        <v-radio value="date" :label="t('credits.grantDialog.onDate')" />
      </v-radio-group>
      <v-text-field
        v-if="expiry === 'date'"
        v-model="date"
        type="date"
        variant="outlined"
        density="comfortable"
        :label="t('credits.grantDialog.date')"
        hide-details
        class="agd__date"
      />
    </fieldset>

    <v-textarea
      v-model="reason"
      autocomplete="off"
      variant="outlined"
      density="comfortable"
      rows="2"
      auto-grow
      :label="t('credits.grantDialog.reason')"
      :hint="t('credits.grantDialog.reasonHint')"
      persistent-hint
      class="agd__reason"
    />

    <v-alert v-if="error" type="error" density="compact" variant="tonal" class="mt-3" role="alert">
      {{ error }}
    </v-alert>
  </AdaptiveDialog>
</template>

<style scoped>
.agd__amount {
  max-width: 240px;
  margin-top: 8px;
}

.agd__group {
  margin: 20px 0 0;
  padding: 0;
  border: 0;
}

.agd__legend {
  padding: 0;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}

.agd__date {
  max-width: 240px;
  margin-top: 12px;
}

.agd__reason {
  margin-top: 24px;
}
</style>
