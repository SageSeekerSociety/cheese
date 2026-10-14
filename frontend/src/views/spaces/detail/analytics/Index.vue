<script setup lang="ts">
// 分析这一层的容器：筛选条件写在地址上、分类列表是取来的、正文哪一格由路由认。画面在
// `IndexView.vue`，这里只留下地址、取数和写回地址那三件事。
import type { Component, VNode } from 'vue'
import type { SpaceAnalyticsQueryState } from './utils'

import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import IndexView from './IndexView.vue'

import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { categories, currentSpaceId } = storeToRefs(spaceStore)

const { filters, resetFilters, replaceFilters } = useSpaceAnalyticsFilters()

const categoryItems = computed(() => [
  { title: t('spaces.analytics.filter.allCategories'), value: null },
  ...categories.value.filter((item) => !item.archivedAt).map((item) => ({ title: item.name, value: item.id })),
])

/** `<router-view>` 的插槽把这一格当 VNode 递下来；交给画面时它是组件。 */
const asPage = (component: VNode | undefined): Component | null => (component as unknown as Component) ?? null

const applyFilters = async (next: SpaceAnalyticsQueryState) => {
  await replaceFilters(next)
}

const handleReset = async () => {
  await resetFilters()
}

watch(
  currentSpaceId,
  (value) => {
    if (value) {
      spaceData.fetchCategories().catch(() => undefined)
    }
  },
  { immediate: true }
)
</script>

<template>
  <router-view v-slot="{ Component: routedPage }">
    <IndexView
      :page="asPage(routedPage)"
      :category-items="categoryItems"
      :filters="filters"
      @apply="applyFilters"
      @reset="handleReset"
    />
  </router-view>
</template>
