<script setup lang="ts">
import type { UsageTeam } from '@/lib/creditUsage'
import type { NavTarget } from '@/lib/navTarget'

import { useI18n } from 'vue-i18n'

import NavLink from '@/components/common/NavLink.vue'
import { fmtPoints, pct, remainingTone } from '@/lib/creditUsage'

// 我所在的团队：每个团队的方案和本月还剩多少，点一行去那个团队的额度页。
defineProps<{
  teams: UsageTeam[]
  /** 每个团队的额度页在哪。 */
  linkOf: (team: UsageTeam) => NavTarget
}>()

const { t, locale } = useI18n()

function left(team: UsageTeam): string {
  if (team.unlimited || team.remaining_ratio === null) return t('usage.teams.unlimited')
  if (team.remaining_ratio <= 0) return t('usage.teams.usedUp')
  if (team.credits_remaining === null) return t('usage.teams.remainingWindow', { pct: pct(team.remaining_ratio) })
  return t('usage.teams.remaining', { n: fmtPoints(team.credits_remaining, locale.value) })
}
</script>

<template>
  <section class="utl" :aria-label="t('usage.teams.title')">
    <h2 class="utl__title t-title">{{ t('usage.teams.title') }}</h2>
    <NavLink v-for="team in teams" :key="team.id" :to="linkOf(team)" class="utl__row">
      <span class="utl__name" data-user-content>{{ team.name }}</span>
      <span class="utl__plan">{{ team.plan.name }}</span>
      <span :class="`utl__left utl__left--${remainingTone(team.remaining_ratio)}`">{{ left(team) }}</span>
      <v-icon icon="mdi-chevron-right" size="16" class="utl__go" aria-hidden="true" />
    </NavLink>
  </section>
</template>

<style scoped>
.utl {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.utl__title {
  margin: 0 0 8px;
  color: var(--ink);
}

.utl__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto 16px;
  gap: 16px;
  align-items: center;
  min-height: 44px;
  padding: 0 8px;
  border-radius: var(--radius-md);
  color: var(--text);
  text-decoration: none;
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.utl__row:hover {
  background: var(--fill);
}

.utl__name {
  overflow: hidden;
  color: var(--ink);
  font-size: 14px;
  line-height: var(--lh-14);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.utl__plan {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.utl__left {
  font-size: 13px;
  line-height: var(--lh-13);
}

.utl__left--low {
  color: var(--warn-ink);
}

.utl__left--out {
  color: var(--danger-ink);
}

.utl__go {
  color: var(--faint);
}
</style>
