<template>
  <PageHeader :title="t('spaces.settings.title')" show-on-mobile>
    <template #tabs>
      <SettingsTabs />
    </template>
  </PageHeader>

  <div class="space-settings">
    <router-view />
    <!-- 只有「分类与话题」一栏往这里放东西：话题管理画在分类下面。 -->
    <router-view name="topics" />
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
  display: flex;
  flex-direction: column;
  gap: 32px;
  padding: 16px;
}
</style>
