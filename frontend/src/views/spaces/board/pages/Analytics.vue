<script setup lang="ts">
// 整板看板挂进新外壳的那一层。九页**一页没重写**，全在老地址上原样服务，
// 这里只是把它们换到新外壳下渲染。
//
// 底下用的是 `analytics/AnalyticsLayout.vue` 而不是 `analytics/Index.vue`：那一页
// 自带一个 `PageHeader`（面包屑按老树的路由 meta 拼，新树拼不出来），新外壳要自己
// 那一条，所以照它原来的写法在这边重建：`PageHeader` + 六格 Tab + Layout。
//
// 和发题页同一个原因要自己装 pinia 的 `space` store：那一整套看板读的是
// `spaceStore.currentSpace`（看板顶上那行空间名）、`currentSpaceId` 与 `categories`
// （筛选栏里的分类下拉），而老树里装它们的是空间壳。不装的话页面能打开，但顶着
// 「当前空间」、分类只有「全部分类」一项 —— 看着像数据丢了。
//
// 六格跳哪儿由 `shellRouteNames.ts` 那一组 provide 说：老树 `SpacesDetailAnalytics*`，
// 新外壳 `SpaceBoardAnalytics*`。
import { computed, provide, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { BOARD_ANALYTICS_ROUTE_NAMES } from '../routeNames'

import PageHeader from '@/components/common/PageHeader.vue'
import { ANALYTICS_ROUTE_NAMES } from '@/lib/shellRouteNames'
import { useSpaceStore } from '@/stores/space'
import AnalyticsLayout from '@/views/spaces/detail/analytics/AnalyticsLayout.vue'
import AnalyticsNavigationTabs from '@/views/spaces/detail/analytics/components/AnalyticsNavigationTabs.vue'

provide(ANALYTICS_ROUTE_NAMES, BOARD_ANALYTICS_ROUTE_NAMES)

const route = useRoute()
const spaceStore = useSpaceStore()

const spaceId = computed(() => Number(route.params.spaceId))
const ready = ref(false)

watch(
  spaceId,
  async (id) => {
    if (!Number.isFinite(id) || id <= 0) return
    ready.value = false
    await spaceStore.fetchSpace(id)
    await spaceStore.fetchCategories()
    ready.value = true
  },
  { immediate: true }
)
</script>

<template>
  <PageHeader icon="mdi-chart-line" title="数据分析">
    <template #tabs>
      <AnalyticsNavigationTabs />
    </template>
  </PageHeader>

  <AnalyticsLayout v-if="ready" />
</template>
