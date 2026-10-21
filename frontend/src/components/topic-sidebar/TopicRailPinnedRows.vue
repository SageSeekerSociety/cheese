<script setup lang="ts">
// 项目名下面那一行：总览和资料库（2026-10-13 用户定：总览回到这一行，摆在资料库
// 上面）。其余的页都在点项目名弹出的菜单里。
//
// 这一行**不长**：每加一格频道就往下挪，几个版本之后又是一摞入口
// （.claude/rules/project-sidebar.md）。摆哪几格由 `lib/shell` 的 `projectPageLayout`
// 算好传进来，点了去哪儿由父级决定。
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { vGuideAnchor } from '@/composables/useGuideAnchors'

import { t } from '@/i18n'

const props = defineProps<{
  /** 这一行的那几页（总览、资料库；壳不摆资料库时只剩首页）。 */
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
  // 鼠标左键按下去了：意图定了，不必再等指针停满 hover 那一百多毫秒（和频道行
  // `TopicRailRow` 的 `press` 同一条理由）。
  (e: 'press-page', key: string): void
  (e: 'cancel-prefetch'): void
}>()

// 点下去到那一页出来之间，这一行一直亮着：只靠按下的那一下，手指抬起后又变回原样，
// 等得久的人会以为没点到（同 TopicRailRow）。
const pendingKey = ref<string | null>(null)
let pendingTimer: number | undefined
function openPage(key: string): void {
  // 点的就是当前这一页：不跳转，也就没有「等它选中」这回事，别亮着不放。
  if (props.routeName === key) {
    emit('open-page', key)
    return
  }
  pendingKey.value = key
  window.clearTimeout(pendingTimer)
  pendingTimer = window.setTimeout(() => (pendingKey.value = null), 10_000)
  emit('open-page', key)
}
watch(
  () => props.routeName,
  () => {
    pendingKey.value = null
    window.clearTimeout(pendingTimer)
  }
)
onBeforeUnmount(() => window.clearTimeout(pendingTimer))

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
  <v-list
    v-if="!page && entries.length"
    density="compact"
    nav
    class="py-0"
    tabindex="-1"
    :aria-label="t('navigation.project.pages')"
  >
    <v-list-item
      v-for="e in entries"
      :key="e.key"
      v-guide-anchor="`page-${e.key}`"
      tabindex="0"
      rounded="lg"
      class="nav-row pinned-row"
      :class="{ 'is-active': e.active, 'is-pending': pendingKey === e.key }"
      :style="{ paddingInlineStart: '8px' }"
      :active="e.active"
      :aria-current="e.active ? 'page' : undefined"
      @click="openPage(e.key)"
      @mouseenter="emit('hover-page', e.key)"
      @mouseleave="emit('cancel-prefetch')"
      @focusin="emit('hover-page', e.key)"
      @focusout="emit('cancel-prefetch')"
      @pointerdown="$event.pointerType === 'mouse' && $event.button === 0 && emit('press-page', e.key)"
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
/* 按下那一帧就换底色：触屏没有 hover，点了之后要等一下才换页。 */
.nav-row:active {
  background: var(--line-2);
}
/* 手指抬起之后到那一页出来之前，这一行保持亮着。 */
.nav-row.is-pending {
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
