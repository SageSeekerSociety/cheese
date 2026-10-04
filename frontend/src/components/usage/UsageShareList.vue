<script setup lang="ts">
import type { NavTarget } from '@/lib/navTarget'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import NavLink from '@/components/common/NavLink.vue'
import { fmtPoints } from '@/lib/creditUsage'

// 本月用量按项目分：每个项目用了多少点。条的长度按用得最多的那个折算。
const props = defineProps<{
  title: string
  /** `credits`：本月用了多少点。 */
  items: { id: string; name: string; credits: number; to?: NavTarget }[]
}>()

const { t, locale } = useI18n()

const top = computed(() => Math.max(0, ...props.items.map((i) => i.credits)))
</script>

<template>
  <section class="usl" :aria-label="title">
    <h2 class="usl__title t-title">{{ title }}</h2>
    <BaseEmptyState v-if="!items.length" size="inline" :title="t('usage.projects.empty')" />
    <div v-for="item in items" :key="item.id" class="usl__row">
      <NavLink v-if="item.to" :to="item.to" class="usl__name">{{ item.name }}</NavLink>
      <span v-else class="usl__name">{{ item.name }}</span>
      <span class="usl__bar" aria-hidden="true">
        <span class="usl__fill" :style="{ width: top > 0 ? `${(item.credits / top) * 100}%` : '0%' }" />
      </span>
      <span class="usl__pct t-num">{{ t('usage.points', { n: fmtPoints(item.credits, locale) }) }}</span>
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

.usl__row {
  display: grid;
  grid-template-columns: minmax(0, 200px) minmax(0, 1fr) 72px;
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

/* 窄屏：名字那一列 200px 加右边的点数把中间的条挤成几个像素 —— 深色主题下
   底色画的轨道几乎看不见，剩下的就是一小截琥珀色浮在行中间，既不像条也说不
   出比例（同 `UsagePackList` 的窄屏版式）。名字独占一行，条从左边起、和点数
   同一行。 */
@media (max-width: 700px) {
  .usl__row {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 6px 16px;
  }
  .usl__name {
    grid-column: 1 / -1;
  }
  .usl__bar {
    min-width: 80px;
  }
}
</style>
