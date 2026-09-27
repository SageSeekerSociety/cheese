<template>
  <v-tabs class="analytics-tabs" color="primary" show-arrows>
    <v-tab
      v-for="tab in tabs"
      :key="tab.name"
      :to="{ name: tab.name, params: { spaceId }, query: route.query }"
      :value="tab.name"
    >
      <v-icon start>{{ tab.icon }}</v-icon>
      {{ tab.label }}
    </v-tab>
  </v-tabs>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { useAnalyticsRouteNames } from '@/lib/shellRouteNames'

const route = useRoute()

const spaceId = computed(() => Number(route.params.spaceId))

/** 六格跳哪儿由挂着它的那棵树说了算（老的九页 / 新题目板外壳各一套名字），
 *  见 `shellRouteNames.ts` 顶部。 */
const names = useAnalyticsRouteNames()

const tabs = [
  { name: names.overview, label: '总览', icon: 'mdi-view-dashboard-outline' },
  { name: names.alerts, label: '告警', icon: 'mdi-bell-alert-outline' },
  { name: names.publishers, label: '老师', icon: 'mdi-account-tie-outline' },
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
