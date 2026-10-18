<script setup lang="ts">
import type { BetaCredits, CreditRequest } from '@/types/creditOffers'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import NavLink from '@/components/common/NavLink.vue'

const props = defineProps<{
  personal?: boolean
  beta?: BetaCredits | null
  requests?: CreditRequest[]
  canManage?: boolean
  busy?: boolean
  error?: string | null
}>()
const emit = defineEmits<{ claim: []; request: [kind: CreditRequest['kind'], credits?: number]; retry: [] }>()
const { t } = useI18n()
const topUp = ref(1000)
const valid = computed(
  () => Number.isInteger(Number(topUp.value)) && Number(topUp.value) >= 100 && Number(topUp.value) <= 1000000
)
const pending = (kind: CreditRequest['kind']) => props.requests?.some((r) => r.kind === kind && r.status === 'pending')
</script>

<template>
  <section class="credit-offers">
    <div v-if="personal" class="credit-offers__offer">
      <div>
        <h2 class="t-title">{{ t('usage.offers.betaTitle') }}</h2>
        <p>{{ t('usage.offers.betaBody', { n: beta?.credits ?? 500 }) }}</p>
        <p>{{ t('usage.offers.feedbackBody', { n: beta?.feedback_credits ?? 100 }) }}</p>
        <NavLink to="/feedback">{{ t('usage.offers.feedbackLink') }}</NavLink>
      </div>
      <BaseButton :disabled="!beta || beta.claimed || busy" :loading="busy" @click="emit('claim')">
        {{ t(beta?.claimed ? 'usage.offers.claimed' : 'usage.offers.claim') }}
      </BaseButton>
    </div>

    <div class="credit-offers__offer">
      <div>
        <span class="credit-offers__eyebrow">Team</span>
        <h2 class="t-title">{{ t('usage.offers.teamTitle') }}</h2>
        <p>{{ t('usage.offers.teamBody') }}</p>
        <p class="credit-offers__privacy">
          <v-icon icon="mdi-shield-check-outline" size="16" />{{ t('usage.offers.privacy') }}
        </p>
      </div>
      <NavLink v-if="personal" to="/teams">{{ t('usage.offers.findTeam') }}</NavLink>
      <BaseButton v-else-if="canManage" :disabled="busy || pending('team_plan')" @click="emit('request', 'team_plan')">
        {{ t(pending('team_plan') ? 'usage.offers.pending' : 'usage.offers.teamApply') }}
      </BaseButton>
      <p v-else class="credit-offers__hint">{{ t('usage.offers.managerOnly') }}</p>
    </div>

    <div v-if="!personal && canManage" class="credit-offers__topup">
      <div>
        <h2 class="t-title">{{ t('usage.offers.topupTitle') }}</h2>
        <p>{{ t('usage.offers.manual') }}</p>
      </div>
      <label class="credit-offers__amount">
        <span>{{ t('usage.offers.amount') }}</span>
        <input v-model.number="topUp" type="number" min="100" max="1000000" step="100" />
      </label>
      <BaseButton :disabled="!valid || busy || pending('top_up')" @click="emit('request', 'top_up', Number(topUp))">
        {{ t(pending('top_up') ? 'usage.offers.pending' : 'usage.offers.topupApply') }}
      </BaseButton>
    </div>
    <p v-if="!personal" class="credit-offers__hint">{{ t('usage.offers.quote') }}</p>
    <ul v-if="requests?.length" class="credit-offers__history">
      <li v-for="item in requests" :key="item.id">
        <span
          >{{ t(item.kind === 'top_up' ? 'usage.offers.topupTitle' : 'usage.offers.teamApply')
          }}<template v-if="item.credits"> · {{ t('usage.points', { n: item.credits }) }}</template></span
        >
        <span>{{ t(`usage.offers.status.${item.status}`) }}</span>
      </li>
    </ul>
    <p v-if="error" class="credit-offers__error" role="alert">
      {{ error }} <button type="button" @click="emit('retry')">{{ t('usage.retry') }}</button>
    </p>
  </section>
</template>

<style scoped>
.credit-offers {
  display: grid;
  gap: 24px;
}
.credit-offers__offer,
.credit-offers__topup {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
}
.credit-offers h2,
.credit-offers p {
  margin: 0;
}
.credit-offers p {
  margin-top: 8px;
  color: var(--muted);
  font-size: 14px;
  line-height: 1.6;
}
.credit-offers__offer > div {
  flex: 1;
}
.credit-offers__eyebrow {
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.08em;
}
.credit-offers__privacy {
  display: flex;
  align-items: center;
  gap: 6px;
}
.credit-offers__amount {
  display: grid;
  gap: 6px;
  color: var(--muted);
  font-size: 12px;
}
.credit-offers__amount input {
  width: 120px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  color: var(--ink);
  background: var(--surface);
  font-size: 16px;
}
.credit-offers__history {
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 13px;
}
.credit-offers__history li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
}
.credit-offers__error,
.credit-offers__error button {
  color: var(--danger);
}
.credit-offers__error button {
  text-decoration: underline;
}
@media (max-width: 700px) {
  .credit-offers__offer,
  .credit-offers__topup {
    flex-direction: column;
    align-items: stretch;
    padding: 20px;
    gap: 16px;
  }
}
</style>
