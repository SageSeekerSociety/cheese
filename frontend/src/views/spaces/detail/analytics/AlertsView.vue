<template>
  <div class="an-section">
    <v-progress-linear v-if="loading && !alerts" indeterminate color="primary" />
    <!-- A failed reload must replace the block, not leave the previous filter's alerts standing (docs/design-system.md §3.10). -->
    <BaseLoadError
      v-if="failed"
      :title="t('spaces.analytics.alerts.loadFailed')"
      :error="errorDetail"
      @retry="emit('load')"
    />
    <AnalyticsAlertGrid v-else-if="alerts" :alerts="alerts" @open="emit('openTasks', $event)" />
    <BaseEmptyState v-else-if="!loading" size="inline" :title="t('spaces.analytics.alerts.empty')" />
  </div>
</template>

<script setup lang="ts">
import type { SpaceAnalyticsAlerts } from '@/types'
import type { SpaceAnalyticsQueryState } from './utils'

import { useI18n } from 'vue-i18n'

import AnalyticsAlertGrid from './components/AnalyticsAlertGrid.vue'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'

// 管理员看板「告警」那一格的画法：几个待办计数，点一个能处理的数就跳去「题目」那一格。
// 取数与跳转在页面里（`Alerts.vue`），这里只吃数据、往上报动作。
defineOptions({ name: 'AlertsView' })

defineProps<{
  loading: boolean
  /** 这一次没读到。和「还没有告警」是两件事：失败留在页面上，空状态才说「暂无」。 */
  failed: boolean
  /** 读失败时服务端原话；取不到时是 null。 */
  errorDetail: string | null
  alerts: SpaceAnalyticsAlerts | null
}>()

const emit = defineEmits<{
  load: []
  /** 点一个能处理的数：报上去「要跳到哪一格、带哪几个筛选」。 */
  openTasks: [patch: Partial<SpaceAnalyticsQueryState>]
}>()

const { t } = useI18n()
</script>

<style scoped src="./analytics.css"></style>
