<template>
  <div class="an-section">
    <v-progress-linear v-if="loading && !alerts" indeterminate color="primary" />
    <AnalyticsAlertGrid v-if="alerts" :alerts="alerts" @open="openTasks" />
    <p v-else-if="!loading" class="an-note">{{ t('spaces.analytics.alerts.empty') }}</p>
  </div>
</template>

<script setup lang="ts">
import type { SpaceAnalyticsAlerts } from '@/network/api/spaces/types'
import type { SpaceAnalyticsQueryState } from './utils'

import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'

import AnalyticsAlertGrid from './components/AnalyticsAlertGrid.vue'
import { useSpaceAnalyticsFilters } from './composables/useSpaceAnalyticsFilters'

import { ANALYTICS_ROUTE_NAMES } from '@/lib/spaceRouteNames'
import { SpacesApi } from '@/network/api/spaces'

const { t } = useI18n()
const { pushToSection, spaceId } = useSpaceAnalyticsFilters()

const loading = ref(false)
const alerts = ref<SpaceAnalyticsAlerts | null>(null)

const load = async () => {
  loading.value = true
  try {
    const { data } = await SpacesApi.getAnalyticsAlerts(spaceId.value)
    alerts.value = data
  } catch (error) {
    console.error('load analytics alerts failed', error)
    toast.error(t('spaces.analytics.alerts.loadFailed'))
  } finally {
    loading.value = false
  }
}

watch(
  spaceId,
  () => {
    load().catch(() => undefined)
  },
  { immediate: true }
)

/** 点一个能处理的数：去「题目」那一格，带上对应的筛选。 */
const openTasks = (patch: Partial<SpaceAnalyticsQueryState>) => pushToSection(ANALYTICS_ROUTE_NAMES.tasks, patch)
</script>

<style scoped src="./analytics.css"></style>
