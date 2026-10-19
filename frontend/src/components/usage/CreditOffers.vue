<script setup lang="ts">
import type { BetaCredits } from '@/types/creditOffers'

import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import NavLink from '@/components/common/NavLink.vue'

defineProps<{ beta?: BetaCredits | null; busy?: boolean; error?: string | null }>()
const emit = defineEmits<{ claim: []; retry: [] }>()
const { t } = useI18n()
</script>

<template>
  <section class="credit-offers" :aria-label="t('usage.rewards.betaTitle')">
    <div class="credit-offers__row">
      <span class="credit-offers__icon"><v-icon icon="mdi-gift-outline" size="20" /></span>
      <div class="credit-offers__copy">
        <h2>{{ t('usage.rewards.betaTitle') }}</h2>
        <p>
          {{ t('usage.rewards.betaOnce') }} <NavLink to="/feedback#rewards">{{ t('usage.rewards.activity') }}</NavLink>
        </p>
      </div>
      <BaseButton
        kind="primary"
        size="sm"
        :disabled="!beta || beta.claimed || busy"
        :loading="busy"
        @click="emit('claim')"
      >
        {{ beta?.claimed ? t('usage.offers.claimed') : t('usage.rewards.claim', { n: beta?.credits ?? 500 }) }}
      </BaseButton>
    </div>
    <p v-if="error" class="credit-offers__error" role="alert">
      {{ error }} <BaseButton size="sm" @click="emit('retry')">{{ t('usage.retry') }}</BaseButton>
    </p>
  </section>
</template>

<style scoped>
.credit-offers {
  border-top: 1px solid var(--line);
  margin-top: 24px;
  padding-top: 16px;
}
.credit-offers__row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.credit-offers__icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  color: var(--accent-ink);
  background: var(--accent-wash);
  border-radius: var(--radius-md);
}
.credit-offers__copy {
  flex: 1;
  min-width: 0;
}
.credit-offers h2 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}
.credit-offers p {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.credit-offers__copy :deep(a) {
  color: var(--text);
  margin-left: 8px;
}
.credit-offers .credit-offers__error {
  color: var(--danger-ink);
}
@media (max-width: 599.98px) {
  .credit-offers__row {
    flex-wrap: wrap;
  }
  .credit-offers__row > .base-btn {
    margin-left: 46px;
  }
}
</style>
