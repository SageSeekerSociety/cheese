<script setup lang="ts">
// 项目名下面那一行：总览和资料库。其余的页都在点项目名弹出的菜单里。
//
// 这一行**不再加东西**：每加一格频道就往下挪，几个版本之后又是一摞入口
// （.claude/rules/project-sidebar.md）。摆哪几格由 `lib/shell` 的 `projectPageLayout`
// 算好传进来，点了去哪儿由父级决定。
import { computed } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{
  /** 这一行的那几页（总览、资料库；壳不摆资料库时只有总览）。 */
  pages: { key: string; label: string; icon: string }[]
  /** 当前页的名字，用来画选中态。 */
  routeName: string | null
  /** 壳换了词之后的项目词汇表。 */
  terms: { project: string; topic: string }
  /** 整页形态（手机）：这几页收进了项目菜单，这一行不画。 */
  page: boolean
}>()

const emit = defineEmits<{
  (e: 'open-page', key: string): void
  (e: 'hover-page', key: string): void
  (e: 'cancel-prefetch'): void
}>()

const entries = computed(() =>
  props.pages.map((p) => ({
    key: p.key,
    label: t(p.label, props.terms),
    icon: p.icon,
    active: props.routeName === p.key,
  }))
)
</script>

<template>
  <!-- 和「# 综合」、频道行同一种行：同一个 16px 图标槽、同一条文字左缘。 -->
  <v-list v-if="!page" density="compact" nav class="py-0" tabindex="-1" :aria-label="t('navigation.project.pages')">
    <v-list-item
      v-for="e in entries"
      :key="e.key"
      tabindex="0"
      rounded="lg"
      class="nav-row pinned-row"
      :class="{ 'is-active': e.active }"
      :style="{ paddingInlineStart: '8px' }"
      :active="e.active"
      :aria-current="e.active ? 'page' : undefined"
      @click="emit('open-page', e.key)"
      @mouseenter="emit('hover-page', e.key)"
      @mouseleave="emit('cancel-prefetch')"
    >
      <template #prepend>
        <span class="row-slot"><v-icon size="16" class="row-glyph" :icon="e.icon" /></span>
      </template>
      <v-list-item-title>{{ e.label }}</v-list-item-title>
    </v-list-item>
  </v-list>
</template>

<style scoped>
/* 行的底子和 TopicRailRootRow 那一份一样（那边说了为什么各写一份）。 */
.nav-row :deep(.v-list-item-title) {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}
.nav-row.is-active {
  background: var(--line-2);
}
.nav-row.is-active :deep(.v-list-item__overlay) {
  opacity: 0 !important;
}
.nav-row.is-active :deep(.v-list-item-title) {
  color: var(--ink);
  font-weight: 600;
}
.nav-row:hover {
  background: var(--fill-2);
}
.nav-row.is-active:hover {
  background: var(--line-2);
}
.nav-row :deep(.v-list-item__spacer) {
  width: 8px !important;
}
.nav-row :deep(.v-list-item__prepend) {
  align-items: center;
}
.row-slot {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
}
.row-glyph {
  color: var(--faint);
}
.nav-row.pinned-row {
  min-height: 36px;
}
</style>
