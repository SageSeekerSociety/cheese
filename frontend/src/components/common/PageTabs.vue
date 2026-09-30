<script setup lang="ts">
// 页头下面那一行页签：每一格是一条子路由（设置的五栏、数据的六栏）。选中的那一格是
// --ink 的字加一条 --ink 的下划线，不用琥珀：它说的是「你在哪」，不是这一页的主操作。
//
// 哪一格是当前的由页面说（`active`），不由链接自己按地址前缀去猜：第一栏的地址往往就是
// 父页面本身，其余几栏都以它开头，按前缀猜它就永远是亮的。
import type { NavTarget } from '@/lib/navTarget'

import NavLink from '@/components/common/NavLink.vue'

defineProps<{
  tabs: ReadonlyArray<{ key: string; label: string; to: NavTarget }>
  active: string | null
  label: string
}>()
</script>

<template>
  <nav class="page-tabs" :aria-label="label">
    <NavLink
      v-for="tab in tabs"
      :key="tab.key"
      :to="tab.to"
      class="page-tabs__tab"
      :class="{ 'page-tabs__tab--on': tab.key === active }"
      :aria-current="tab.key === active ? 'page' : undefined"
    >
      {{ tab.label }}
    </NavLink>
  </nav>
</template>

<style scoped>
.page-tabs {
  display: flex;
  gap: 20px;
  padding: 0 16px;
  overflow-x: auto;
  border-bottom: 1px solid var(--line);
  scrollbar-width: none;
}

.page-tabs__tab {
  flex: none;
  margin-bottom: -1px;
  padding: 10px 0;
  border-bottom: 2px solid transparent;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  text-decoration: none;
  white-space: nowrap;
  transition: color var(--dur-quick) var(--ease-standard);
}

.page-tabs__tab:hover {
  color: var(--ink);
}

.page-tabs__tab:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}

.page-tabs__tab--on {
  border-bottom-color: var(--ink);
  color: var(--ink);
  font-weight: 600;
}
</style>
