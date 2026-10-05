<template>
  <PageHeader :title="t('spaces.analytics.title')" />
  <AnalyticsNavigationTabs />
  <AnalyticsLayout
    v-model="model"
    :category-items="categoryItems"
    @apply="emit('apply')"
    @reset="emit('reset')"
    @apply-preset="emit('applyPreset', $event)"
  />
</template>

<script setup lang="ts">
// 数据看板的画面：标题、几格之间的切换、以及带筛选条的骨架。取数、读地址都在页面
// `Index.vue` 那一侧（场景规则见 docs/manual/dev/scenes.md）。
import type { SpaceAnalyticsQueryState } from './utils'

import { useI18n } from 'vue-i18n'

import AnalyticsNavigationTabs from './components/AnalyticsNavigationTabs.vue'
import AnalyticsLayout from './AnalyticsLayout.vue'

import PageHeader from '@/components/common/PageHeader.vue'

defineProps<{
  categoryItems: Array<{ title: string; value: number | null }>
}>()

const model = defineModel<SpaceAnalyticsQueryState>({ required: true })

const emit = defineEmits<{
  apply: []
  reset: []
  applyPreset: [preset: '30d' | '180d' | 'all']
}>()

const { t } = useI18n()
</script>
