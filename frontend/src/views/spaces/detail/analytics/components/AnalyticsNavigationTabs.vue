<template>
  <PageTabs :tabs="tabs" :active="active" :label="t('spaces.analytics.title')" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { useNavigation } from '@/composables/useNavigation'

import PageTabs from '@/components/common/PageTabs.vue'
import { ANALYTICS_ROUTE_NAMES } from '@/lib/spaceRouteNames'

const { t } = useI18n()
const nav = useNavigation()

const spaceId = computed(() => Number(nav?.route?.params?.spaceId))
/** 换一格不丢当前那一串筛选条件（query 原样带过去）。 */
const query = computed(() => nav?.route?.query ?? {})

const KEYS = ['overview', 'alerts', 'publishers', 'tasks', 'participants', 'learning'] as const

const tabs = computed(() =>
  KEYS.map((key) => ({
    key: ANALYTICS_ROUTE_NAMES[key],
    label: t(`spaces.analytics.tabs.${key}`),
    to: { name: ANALYTICS_ROUTE_NAMES[key], params: { spaceId: spaceId.value }, query: query.value },
  }))
)

// 当前这一格按路由名认：「总览」的地址就是数据页本身，按前缀认它在哪一格都亮着。
const active = computed(() => {
  const name = nav?.route?.name
  return typeof name === 'string' ? name : null
})
</script>
