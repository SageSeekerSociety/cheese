<template>
  <div class="analytics-layout">
    <AnalyticsFilterBar
      v-model="model"
      :category-items="categoryItems"
      @apply="emit('apply')"
      @reset="emit('reset')"
      @apply-preset="emit('applyPreset', $event)"
    />

    <div class="analytics-layout__body">
      <router-view v-slot="{ Component }">
        <transition name="fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </div>
  </div>
</template>

<script setup lang="ts">
// 数据看板的公共骨架：一行筛选 + 底下各格的路由出口。原来这两个取数点——把筛选写回
// 地址、拉分类——留在这里，把骨架拖成了 C 级。现在它只收分类选项和一份草稿筛选，
// 把「应用/重置/按预设」发出去；写地址和拉数归页面（场景规则见 docs/manual/dev/scenes.md）。
import type { SpaceAnalyticsQueryState } from './utils'

import AnalyticsFilterBar from './components/AnalyticsFilterBar.vue'

defineProps<{
  categoryItems: Array<{ title: string; value: number | null }>
}>()

const model = defineModel<SpaceAnalyticsQueryState>({ required: true })

const emit = defineEmits<{
  apply: []
  reset: []
  applyPreset: [preset: '30d' | '180d' | 'all']
}>()
</script>

<style scoped>
.analytics-layout {
  padding: 16px 16px 48px;
}

.analytics-layout__body {
  margin-top: 24px;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--dur-quick) var(--ease-standard);
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
