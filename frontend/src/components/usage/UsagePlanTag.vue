<script setup lang="ts">
import type { UsagePlan } from '@/lib/creditUsage'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { fmtPoints } from '@/lib/creditUsage'

// 额度页上方案名那个小标签。点开看这个方案包含什么：每月多少点，或有哪些使用上限；
// 能用哪些模型。
const props = defineProps<{ plan: UsagePlan }>()

const { t, locale } = useI18n()

const sep = computed(() => (locale.value === 'en' ? ', ' : '、'))

const terms = computed(() => {
  const plan = props.plan
  if (plan.unlimited) return t('usage.plan.unlimited')
  if (plan.windows.length) {
    const list = plan.windows.map((w) => {
      if (w.calendar === 'week') return t('usage.plan.week')
      if (w.calendar === 'month') return t('usage.plan.month')
      return t('usage.plan.hours', { hours: w.hours })
    })
    return t('usage.plan.windows', { list: list.join(sep.value) })
  }
  return t('usage.plan.monthly', { n: fmtPoints(plan.credits_per_period ?? 0, locale.value) })
})
</script>

<template>
  <v-menu location="bottom start" :close-on-content-click="false">
    <template #activator="{ props: menu }">
      <button v-bind="menu" type="button" class="upt" :aria-label="t('usage.plan.label', { name: plan.name })">
        {{ plan.name }}
        <v-icon icon="mdi-chevron-down" size="14" aria-hidden="true" />
      </button>
    </template>
    <div class="upt__card">
      <p class="upt__name">{{ plan.name }}</p>
      <p class="upt__terms">{{ terms }}</p>
      <p class="upt__label">{{ t('usage.plan.models') }}</p>
      <p class="upt__models">
        {{ plan.models.length ? plan.models.join(sep) : t('usage.plan.noModels') }}
      </p>
    </div>
  </v-menu>
</template>

<style scoped>
.upt {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 0 4px 0 6px;
  background: var(--fill-2);
  border: 0;
  border-radius: var(--radius-sm);
  color: var(--muted);
  font: inherit;
  font-size: 12px;
  font-weight: 500;
  line-height: var(--lh-12);
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.upt:hover {
  background: var(--fill);
  color: var(--text);
}

.upt__card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 280px;
  max-width: calc(100vw - 32px);
  padding: 12px 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-2);
}

.upt__card p {
  margin: 0;
}

.upt__name {
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}

.upt__terms,
.upt__models {
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}

.upt__card .upt__label {
  margin-top: 8px;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
</style>
