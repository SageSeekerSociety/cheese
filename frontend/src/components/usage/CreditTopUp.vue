<script setup lang="ts">
import type { CreditRequest } from '@/types/creditOffers'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'

const props = defineProps<{ requests?: CreditRequest[]; busy?: boolean; error?: string | null }>()
const emit = defineEmits<{ request: [credits: number] }>()
const { t } = useI18n()
const open = ref(false)
const amount = ref(1000)
const pending = computed(() => props.requests?.some((item) => item.kind === 'top_up' && item.status === 'pending'))
const valid = computed(() => Number.isInteger(amount.value) && amount.value >= 100 && amount.value <= 1000000)
watch(pending, (value) => {
  if (value) open.value = false
})
</script>

<template>
  <BaseButton kind="primary" prepend-icon="mdi-plus" :disabled="pending" @click="open = true">{{
    t(pending ? 'usage.offers.pending' : 'usage.rewards.topUp')
  }}</BaseButton>
  <AdaptiveDialog
    v-model="open"
    size="sm"
    :title="t('usage.offers.topupTitle')"
    :primary-label="t('usage.rewards.submitRequest')"
    :primary-disabled="!valid"
    :primary-loading="busy"
    :close-disabled="busy"
    @primary="emit('request', amount)"
  >
    <p class="topup-note">{{ t('usage.offers.manual') }}</p>
    <BaseField class="topup-field" :label="t('usage.offers.amount')" :hint="t('usage.rewards.topUpRange')" required>
      <template #default="{ id, describedby }"
        ><input
          :id="id"
          v-model.number="amount"
          :aria-describedby="describedby"
          aria-required="true"
          type="number"
          min="100"
          max="1000000"
          step="100"
      /></template>
    </BaseField>
    <p class="topup-note">{{ t('usage.rewards.requestOnly') }}</p>
    <p v-if="error" class="topup-error" role="alert">{{ error }}</p>
  </AdaptiveDialog>
</template>

<style scoped>
.topup-note {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  margin: 0 0 24px;
}
.topup-field {
  display: grid;
  gap: 8px;
  margin: 0 0 24px;
  font-size: 14px;
  line-height: var(--lh-14);
}
.topup-field input {
  width: 100%;
  min-height: 44px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  padding: 8px 12px;
  color: var(--ink);
  background: var(--surface);
  font: inherit;
}
.topup-error {
  color: var(--danger-ink);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
