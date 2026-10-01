<template>
  <div class="analytics-layout">
    <AnalyticsFilterBar
      v-model="draftFilters"
      :category-items="categoryItems"
      @apply="applyFilters"
      @reset="handleReset"
      @apply-preset="applyPreset"
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
import type { SpaceAnalyticsQueryState } from './utils'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import AnalyticsFilterBar from './components/AnalyticsFilterBar.vue'
import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'
import { formatUtcDate, inferAnalyticsGroupBy } from './utils'

import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { categories, currentSpaceId } = storeToRefs(spaceStore)

const { filters, resetFilters, replaceFilters } = useSpaceAnalyticsFilters()

const draftFilters = ref<SpaceAnalyticsQueryState>({ ...filters.value })

watch(
  filters,
  (value) => {
    draftFilters.value = { ...value }
  },
  { immediate: true }
)

const categoryItems = computed(() => [
  { title: t('spaces.analytics.filter.allCategories'), value: null },
  ...categories.value.filter((item) => !item.archivedAt).map((item) => ({ title: item.name, value: item.id })),
])

const applyFilters = async () => {
  await replaceFilters({
    ...draftFilters.value,
    groupBy: inferAnalyticsGroupBy(draftFilters.value.from, draftFilters.value.to),
  })
}

const handleReset = async () => {
  await resetFilters()
}

const applyPreset = async (preset: '30d' | '180d' | 'all') => {
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

  await applyFilters()
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
