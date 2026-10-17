<!--
  分析这一层的画面：页头、六格导航、筛选栏，正文那块的换页动画。筛选条件落在地址上、
  分类列表是取来的、正文哪一格由路由认 —— 这三件事都在容器 `Index.vue` 那边，这里只收
  props、把要办的事交上去。
-->
<script setup lang="ts">
import type { Component } from 'vue'
import type { SpaceAnalyticsQueryState } from './utils'

import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AnalyticsFilterBar from './components/AnalyticsFilterBar.vue'
import AnalyticsNavigationTabs from './components/AnalyticsNavigationTabs.vue'
import { formatUtcDate, inferAnalyticsGroupBy } from './utils'

import PageHeader from '@/components/common/PageHeader.vue'

const props = defineProps<{
  /** 正文那一格。页面从 `<router-view>` 的插槽里认出来，再递进来。 */
  page: Component | null
  categoryItems: Array<{ title: string; value: number | null }>
  /** 已经落定的筛选条件（页面把它们写在地址上）。 */
  filters: SpaceAnalyticsQueryState
}>()

const emit = defineEmits<{
  /** 筛选栏按了「应用」：带上改完的那一份交上去，由页面写回地址。 */
  apply: [filters: SpaceAnalyticsQueryState]
  reset: []
}>()

const { t } = useI18n()

/** 筛选栏里正在改的那一份。地址上那份换了才跟上，改到一半的不丢。 */
const draftFilters = ref<SpaceAnalyticsQueryState>({ ...props.filters })

watch(
  () => props.filters,
  (value) => {
    draftFilters.value = { ...value }
  },
  { immediate: true }
)

const applyFilters = () => {
  emit('apply', {
    ...draftFilters.value,
    groupBy: inferAnalyticsGroupBy(draftFilters.value.from, draftFilters.value.to),
  })
}

const handleReset = () => {
  emit('reset')
}

/** 三个预设是一段日期：先落到草稿上，再按「应用」交上去。 */
const applyPreset = (preset: '30d' | '180d' | 'all') => {
  const toDate = new Date()
  const fromDate = new Date()

  if (preset === '30d') {
    fromDate.setUTCDate(fromDate.getUTCDate() - 30)
  } else if (preset === '180d') {
    fromDate.setUTCDate(fromDate.getUTCDate() - 180)
  } else {
    fromDate.setUTCFullYear(1970, 0, 1)
  }

  draftFilters.value = {
    ...draftFilters.value,
    from: formatUtcDate(fromDate),
    to: formatUtcDate(toDate),
  }

  applyFilters()
}
</script>

<template>
  <PageHeader :title="t('spaces.analytics.title')" />
  <AnalyticsNavigationTabs />
  <div class="analytics-layout">
    <AnalyticsFilterBar
      v-model="draftFilters"
      :category-items="categoryItems"
      @apply="applyFilters"
      @reset="handleReset"
      @apply-preset="applyPreset"
    />

    <div class="analytics-layout__body">
      <transition name="fade" mode="out-in">
        <component :is="page" />
      </transition>
    </div>
  </div>
</template>

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
