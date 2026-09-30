<template>
  <v-tabs class="analytics-tabs" color="primary" show-arrows>
    <v-tab v-for="tab in tabs" :key="tab.name" :to="{ name: tab.name, params: { spaceId }, query }" :value="tab.name">
      <v-icon start>{{ tab.icon }}</v-icon>
      {{ tab.label }}
    </v-tab>
  </v-tabs>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { useNavigation } from '@/composables/useNavigation'

import { ANALYTICS_ROUTE_NAMES } from '@/lib/spaceRouteNames'

const nav = useNavigation()

const spaceId = computed(() => Number(nav?.route?.params?.spaceId))
/** 换一格不丢当前那一串筛选条件（query 原样带过去）。 */
const query = computed(() => nav?.route?.query ?? {})

const names = ANALYTICS_ROUTE_NAMES

const tabs = [
  { name: names.overview, label: '总览', icon: 'mdi-view-dashboard-outline' },
  { name: names.alerts, label: '告警', icon: 'mdi-bell-alert-outline' },
  { name: names.publishers, label: '出题人', icon: 'mdi-account-tie-outline' },
  { name: names.tasks, label: '题目', icon: 'mdi-clipboard-text-outline' },
  { name: names.participants, label: '参与者', icon: 'mdi-account-group-outline' },
  { name: names.learning, label: '学习', icon: 'mdi-school-outline' },
]
</script>

<style scoped lang="scss">
.analytics-tabs {
  :deep(.v-slide-group__content) {
    gap: 6px;
  }

  :deep(.v-tab) {
    border-radius: 8px;
    min-height: 40px;
    text-transform: none;
    font-weight: 500;
    letter-spacing: 0;
  }
}
</style>
