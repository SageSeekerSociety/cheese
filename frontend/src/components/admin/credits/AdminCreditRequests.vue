<script setup lang="ts">
import type { Plan } from '@/lib/adminCredits'
import type { CreditRequest } from '@/types/creditOffers'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'

defineProps<{ requests: CreditRequest[]; plans: Plan[]; busy: boolean; error: string | null }>()
const emit = defineEmits<{
  handle: [id: string, approve: boolean, paymentConfirmed: boolean, planKey?: string]
  retry: []
}>()
const { t } = useI18n()
const confirmed = ref<Record<string, boolean>>({})
const chosen = ref<Record<string, string>>({})
</script>

<template>
  <section class="credit-requests">
    <h2 class="t-title">{{ t('usage.offers.requestsTitle') }}</h2>
    <p v-if="error" role="alert">
      {{ error }} <BaseButton @click="emit('retry')">{{ t('usage.retry') }}</BaseButton>
    </p>
    <p v-else-if="!requests.length">{{ t('usage.offers.emptyRequests') }}</p>
    <article v-for="item in requests" :key="item.id" class="credit-requests__row">
      <div>
        <strong>{{ item.team_name }}</strong>
        <p>
          {{ t(item.kind === 'top_up' ? 'usage.offers.topupTitle' : 'usage.offers.teamApply')
          }}<template v-if="item.credits"> · {{ t('usage.points', { n: item.credits }) }}</template> ·
          {{ item.requester_handle }}
        </p>
      </div>
      <label v-if="item.kind === 'team_plan'"
        >{{ t('usage.offers.choosePlan') }}
        <select v-model="chosen[item.id]">
          <option value="" disabled>{{ t('usage.offers.choosePlan') }}</option>
          <option
            v-for="plan in plans.filter((p) => !p.admin_only && p.audience !== 'personal')"
            :key="plan.key"
            :value="plan.key"
          >
            {{ plan.name }}
          </option>
        </select>
      </label>
      <label class="credit-requests__check"
        ><input v-model="confirmed[item.id]" type="checkbox" />{{ t('usage.offers.confirmPayment') }}</label
      >
      <div class="credit-requests__actions">
        <BaseButton
          kind="primary"
          size="sm"
          :disabled="busy || !confirmed[item.id] || (item.kind === 'team_plan' && !chosen[item.id])"
          @click="emit('handle', item.id, true, !!confirmed[item.id], chosen[item.id])"
          >{{ t('usage.offers.approve') }}</BaseButton
        >
        <BaseButton kind="danger" size="sm" :disabled="busy" @click="emit('handle', item.id, false, false)">{{
          t('usage.offers.decline')
        }}</BaseButton>
      </div>
    </article>
  </section>
</template>

<style scoped>
.credit-requests {
  display: grid;
  gap: 16px;
}
.credit-requests h2,
.credit-requests p {
  margin: 0;
}
.credit-requests p {
  color: var(--muted);
  font-size: 13px;
}
.credit-requests__row {
  display: grid;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.credit-requests__row label {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 13px;
}
.credit-requests select {
  padding: 6px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  color: var(--ink);
  background: var(--surface);
}
.credit-requests__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
