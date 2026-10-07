<script setup lang="ts">
import type { UsagePack } from '@/lib/creditUsage'

import { useI18n } from 'vue-i18n'

import { fmtMonthDay, fmtPoints } from '@/lib/creditUsage'

// 方案之外的额度：管理员发放、购买、题目给项目的定向额度，各剩多少点、共多少点、
// 何时到期，以及什么时候用到它：题目给项目的最先用，其余在方案额度用完后用。
defineProps<{ packs: UsagePack[] }>()

const { t, locale } = useI18n()

function name(pack: UsagePack): string {
  if (pack.source === 'admin_grant') return t('usage.packs.grant')
  if (pack.source === 'purchase') return t('usage.packs.purchase')
  if (pack.source === 'task_earmark') {
    return pack.task_name ? t('usage.packs.task', { task: pack.task_name }) : t('usage.packs.taskUnknown')
  }
  return t('usage.packs.other')
}

function meta(pack: UsagePack): string {
  const parts = [
    pack.source === 'task_earmark' ? t('usage.packs.first') : t('usage.packs.afterPlan'),
    t('usage.packs.total', { n: fmtPoints(pack.credits_total, locale.value) }),
    pack.expires_at
      ? t('usage.packs.expires', { date: fmtMonthDay(pack.expires_at, locale.value) })
      : t('usage.packs.noExpiry'),
  ]
  if (pack.project_name) parts.unshift(t('usage.packs.project', { project: pack.project_name }))
  return parts.join(' · ')
}
</script>

<template>
  <section class="upk" :aria-label="t('usage.packs.title')">
    <h2 class="upk__title t-title">{{ t('usage.packs.title') }}</h2>
    <div v-for="pack in packs" :key="pack.id" class="upk__row">
      <span class="upk__name">
        <span class="upk__main">{{ name(pack) }}</span>
        <span class="upk__meta">{{ meta(pack) }}</span>
      </span>
      <span class="upk__bar" aria-hidden="true">
        <span class="upk__fill" :style="{ width: `${pack.remaining_ratio * 100}%` }" />
      </span>
      <span class="upk__left t-num">{{
        t('usage.packs.remaining', { n: fmtPoints(pack.credits_remaining, locale) })
      }}</span>
    </div>
  </section>
</template>

<style scoped>
.upk {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.upk__title {
  margin: 0;
  color: var(--ink);
}

.upk__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(80px, 160px) auto;
  gap: 16px;
  align-items: center;
}

.upk__name {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.upk__main {
  overflow: hidden;
  color: var(--ink);
  font-size: 14px;
  line-height: var(--lh-14);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upk__meta {
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

.upk__bar {
  display: block;
  height: 6px;
  overflow: hidden;
  background: var(--fill-2);
  border-radius: var(--radius-pill);
}

.upk__fill {
  display: block;
  height: 100%;
  background: var(--muted);
}

.upk__left {
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
}
/* 断点收进共享 token：767.98 = $bp-phone（styles/breakpoints.scss）。 */
@media (max-width: 767.98px) {
  .upk__row {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 6px 16px;
  }

  .upk__name {
    grid-column: 1 / -1;
  }
}
</style>
