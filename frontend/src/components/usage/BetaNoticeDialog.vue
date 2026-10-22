<script setup lang="ts">
/**
 * 「知是正在内测」那一次说明（接线在 `composables/useBetaNotice.ts`，挂在
 * `layouts/AppShell.vue`）。一张安静的表：每样额度多少、怎么得，数都从接口来——
 * 每月方案是这个人自己的方案，按时间窗口计或不限的方案没有这一行。
 * 还没领内测体验额度的，主按钮是领取；领过的只剩「知道了」。
 */
import type { BetaCredits } from '@/types/creditOffers'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { DIALOG_WIDTH } from '@/components/base/dialogSize'
import { t } from '@/i18n'
import { docsUrl } from '@/lib/docsSite'

const props = defineProps<{
  open: boolean
  offer: BetaCredits
  claiming?: boolean
  claimFailed?: boolean
}>()

const emit = defineEmits<{ claim: []; dismiss: [] }>()

const points = (n: number) => t('account.betaNotice.points', { n: n.toLocaleString('en-US') })

const rows = computed(() => {
  const o = props.offer
  return [
    ...(o.plan_credits == null
      ? []
      : [
          {
            what: t('account.betaNotice.plan'),
            amount: points(o.plan_credits),
            rule: t('account.betaNotice.planRule'),
          },
        ]),
    {
      what: t('account.betaNotice.beta'),
      amount: points(o.credits),
      rule: o.claimed ? t('account.betaNotice.betaClaimed') : t('account.betaNotice.betaRule'),
    },
    {
      what: t('account.betaNotice.feedback'),
      amount: points(o.feedback_credits),
      rule: t('account.betaNotice.feedbackRule'),
    },
    {
      what: t('account.betaNotice.invite'),
      amount: points(o.invite_credits),
      rule: t('account.betaNotice.inviteRule', { n: o.invitee_credits.toLocaleString('en-US') }),
    },
  ]
})

const DOCS = docsUrl('/rewards')
</script>

<template>
  <v-dialog
    :model-value="open"
    :max-width="DIALOG_WIDTH.md"
    @update:model-value="(v: boolean) => !v && emit('dismiss')"
  >
    <v-card rounded="lg" class="beta-notice" aria-labelledby="beta-notice-title">
      <p class="beta-notice__eyebrow">{{ t('account.betaNotice.eyebrow') }}</p>
      <h2 id="beta-notice-title" class="t-dialog-title beta-notice__title">{{ t('account.betaNotice.title') }}</h2>
      <p class="t-body-readable beta-notice__body">{{ t('account.betaNotice.body') }}</p>
      <dl class="beta-notice__list">
        <div v-for="row in rows" :key="row.what" class="beta-notice__row">
          <dt class="beta-notice__what">{{ row.what }}</dt>
          <dd class="beta-notice__amount t-num">{{ row.amount }}</dd>
          <dd class="beta-notice__rule">{{ row.rule }}</dd>
        </div>
      </dl>
      <p class="beta-notice__foot">{{ t('account.betaNotice.lifetime') }}</p>
      <p v-if="claimFailed" class="beta-notice__error" role="alert">{{ t('account.betaNotice.claimFailed') }}</p>
      <div class="beta-notice__actions">
        <a class="beta-notice__link" :href="DOCS" target="_blank" rel="noopener">{{ t('account.betaNotice.more') }}</a>
        <span class="beta-notice__buttons">
          <template v-if="offer.claimed">
            <BaseButton kind="primary" @click="emit('dismiss')">{{ t('account.betaNotice.ok') }}</BaseButton>
          </template>
          <template v-else>
            <BaseButton @click="emit('dismiss')">{{ t('account.betaNotice.later') }}</BaseButton>
            <BaseButton kind="primary" :loading="claiming" @click="emit('claim')">
              {{ t('account.betaNotice.claim', { n: offer.credits.toLocaleString('en-US') }) }}
            </BaseButton>
          </template>
        </span>
      </div>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.beta-notice {
  padding: 24px 24px 16px;
}

.beta-notice__eyebrow {
  margin: 0 0 6px;
  font-size: 12px;
  line-height: var(--lh-12);
  letter-spacing: 0.04em;
  color: var(--muted);
}

.beta-notice__title {
  padding: 0;
  margin: 0 0 8px;
}

.beta-notice__body {
  margin: 0 0 16px;
  color: var(--text);
}

.beta-notice__list {
  margin: 0;
  border-top: 1px solid var(--line);
}

.beta-notice__row {
  display: grid;
  grid-template-columns: 7.5em 5.5em minmax(0, 1fr);
  gap: 12px;
  align-items: baseline;
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
  font-size: 14px;
  line-height: var(--lh-14);
}

.beta-notice__what {
  color: var(--text);
}

.beta-notice__amount {
  margin: 0;
  font-weight: 600;
  color: var(--text);
}

.beta-notice__rule {
  margin: 0;
  color: var(--muted);
}

.beta-notice__foot {
  margin: 10px 0 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.beta-notice__error {
  margin: 12px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--danger);
}

.beta-notice__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  align-items: center;
  justify-content: space-between;
  margin-top: 20px;
}

.beta-notice__buttons {
  display: inline-flex;
  gap: 8px;
  margin-left: auto;
}

.beta-notice__link {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  text-decoration: underline;
  text-underline-offset: 3px;
}

@media (max-width: 599.98px) {
  .beta-notice {
    padding: 20px 20px 16px;
  }

  .beta-notice__row {
    grid-template-columns: minmax(0, 1fr) auto;
    row-gap: 2px;
  }

  .beta-notice__rule {
    grid-column: 1 / -1;
    font-size: 12px;
  }
}
</style>
