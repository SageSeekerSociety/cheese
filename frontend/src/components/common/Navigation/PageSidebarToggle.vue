<script setup lang="ts">
// 宽档里侧栏收起后展开它的按钮：画在这一页页头的最左边。侧栏开着、平板那一档（开关在顶栏）
// 和手机上不画。展开这件事由外壳交进来（appNav 的 `unfoldSidebar`）；没有外壳时也不画。
import { computed, onActivated, onBeforeUnmount, onDeactivated, ref, watch } from 'vue'

import { useAppNav } from './appNav'
import { pageSidebarToggles } from './sidebarToggle'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const nav = useAppNav()
const unfold = computed(() => nav?.unfoldSidebar.value ?? null)

// 留在内存里但不在屏幕上的页面（keep-alive）不算数。
const onScreen = ref(true)
onActivated(() => (onScreen.value = true))
onDeactivated(() => (onScreen.value = false))
const shown = computed(() => onScreen.value && unfold.value !== null)
let counted = false
function count(now: boolean) {
  if (now === counted) return
  counted = now
  pageSidebarToggles.value += now ? 1 : -1
}
watch(shown, count, { immediate: true })
onBeforeUnmount(() => count(false))
</script>

<template>
  <BaseButton
    v-if="unfold"
    kind="ghost"
    size="sm"
    icon="mdi-page-layout-sidebar-left"
    class="page-sidebar-toggle"
    data-sidebar-toggle
    aria-expanded="false"
    :aria-label="t('navigation.sidebar.expand')"
    :title="t('navigation.sidebar.expand')"
    @click="unfold()"
  />
</template>

<style scoped>
.page-sidebar-toggle {
  flex: none;
}
</style>
