<template>
  <PageHeader :title="t('spaces.analytics.title')" />
  <AnalyticsNavigationTabs />
  <AnalyticsLayout />
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import AnalyticsNavigationTabs from './components/AnalyticsNavigationTabs.vue'
import AnalyticsLayout from './AnalyticsLayout.vue'

import PageHeader from '@/components/common/PageHeader.vue'

const { t } = useI18n()
</script>
