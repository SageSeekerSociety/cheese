<script setup lang="ts">
import type { NavTarget } from '@/lib/navTarget'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import NavLink from '@/components/common/NavLink.vue'
import { pct } from '@/lib/creditUsage'

// 本月用量按项目分：每个项目占多少。条的长度按占比最大的那个折算。
const props = defineProps<{
  title: string
  items: { id: string; name: string; share: number; to?: NavTarget }[]
}>()

const { t } = useI18n()

const top = computed(() => Math.max(0, ...props.items.map((i) => i.share)))
</script>

<template>
  <section class="usl" :aria-label="title">
    <h2 class="usl__title t-title">{{ title }}</h2>
    <p v-if="!items.length" class="usl__empty">{{ t('usage.projects.empty') }}</p>
    <div v-for="item in items" :key="item.id" class="usl__row">
      <NavLink v-if="item.to" :to="item.to" class="usl__name">{{ item.name }}</NavLink>
      <span v-else class="usl__name">{{ item.name }}</span>
      <span class="usl__bar" aria-hidden="true">
        <span class="usl__fill" :style="{ width: top > 0 ? `${(item.share / top) * 100}%` : '0%' }" />
      </span>
      <span class="usl__pct t-num">{{ pct(item.share) }}</span>
    </div>
  </section>
</template>

<style scoped>
.usl {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.usl__title {
  margin: 0;
  color: var(--ink);
}

.usl__empty {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.usl__row {
  display: grid;
  grid-template-columns: minmax(0, 200px) minmax(0, 1fr) 48px;
  gap: 16px;
  align-items: center;
}

.usl__name {
  overflow: hidden;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  text-decoration: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}

a.usl__name:hover {
  color: var(--ink);
  text-decoration: underline;
}

.usl__bar {
  display: block;
  height: 6px;
  overflow: hidden;
  background: var(--fill-2);
  border-radius: var(--radius-pill);
}

.usl__fill {
  display: block;
  height: 100%;
  background: var(--usage-collab);
}

.usl__pct {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  text-align: right;
}
</style>
