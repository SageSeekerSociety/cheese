<template>
  <PageHeader :title="t('spaces.settings.title')" show-on-mobile>
    <template #tabs>
      <SettingsTabs />
    </template>
  </PageHeader>

  <div class="space-settings">
    <router-view />
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import SettingsTabs from './SettingsTabs.vue'

import PageHeader from '@/components/common/PageHeader.vue'

const { t } = useI18n()
</script>

<style scoped>
.space-settings {
  padding: 16px;
}
</style>
