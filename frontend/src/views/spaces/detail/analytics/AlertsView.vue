<template>
  <div class="an-section">
    <v-progress-linear v-if="loading && !alerts" indeterminate color="primary" />
    <!-- A failed reload must replace the block, not leave the previous filter's alerts standing (docs/design-system.md §3.10). -->
    <BaseLoadError
      v-if="failed"
      :title="t('spaces.analytics.alerts.loadFailed')"
      :error="errorDetail"
      @retry="emit('retry')"
    />
    <AnalyticsAlertGrid v-else-if="alerts" :alerts="alerts" @open="(patch) => emit('openTasks', patch)" />
    <BaseEmptyState v-else-if="!loading" size="inline" :title="t('spaces.analytics.alerts.empty')" />
  </div>
</template>

<script setup lang="ts">
// 告警这一格的画面：读到的告警、失败的原因、还没读到的样子。取数和「去题目」归页面
// `Alerts.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type { SpaceAnalyticsAlerts } from '@/network/api/spaces/types'
import type { SpaceAnalyticsQueryState } from './utils'

import { useI18n } from 'vue-i18n'

import AnalyticsAlertGrid from './components/AnalyticsAlertGrid.vue'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'

defineProps<{
  alerts: SpaceAnalyticsAlerts | null
  loading: boolean
  failed: boolean
  errorDetail: string | null
}>()

const emit = defineEmits<{
  retry: []
  openTasks: [patch: Partial<SpaceAnalyticsQueryState>]
}>()

const { t } = useI18n()
</script>

<style scoped src="./analytics.css"></style>
