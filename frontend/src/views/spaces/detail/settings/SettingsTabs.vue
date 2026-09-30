<template>
  <PageTabs :tabs="tabs" :active="active" :label="t('spaces.settings.title')" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import PageTabs from '@/components/common/PageTabs.vue'

const { t } = useI18n()
const route = useRoute()

/** 设置的五栏，各是 `manage/settings` 下的一条子路由。 */
const TABS = [
  { name: 'SpacesDetailSettingsBasic', label: 'spaces.settings.tabs.basic' },
  { name: 'SpacesDetailSettingsCategories', label: 'spaces.settings.tabs.categories' },
  { name: 'SpacesDetailSettingsTemplates', label: 'spaces.settings.tabs.templates' },
  { name: 'SpacesDetailSettingsInviteCodes', label: 'spaces.settings.tabs.inviteCodes' },
  { name: 'SpacesDetailSettingsDomainGroups', label: 'spaces.settings.tabs.domainGroups' },
]

const tabs = computed(() =>
  TABS.map((tab) => ({
    key: tab.name,
    label: t(tab.label),
    to: { name: tab.name, params: { spaceId: route.params.spaceId } },
  }))
)

// 当前这一栏按路由名认，不按地址前缀：「基本信息」的地址就是设置页本身。
const active = computed(() => (typeof route.name === 'string' ? route.name : null))
</script>
