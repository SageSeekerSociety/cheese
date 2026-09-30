<template>
  <v-tabs class="settings-tabs" color="primary" show-arrows>
    <!-- exact：「基本信息」的地址就是设置页本身，其余几栏都在它下面；按前缀认选中，
         它在哪一栏都亮着。 -->
    <v-tab v-for="tab in tabs" :key="tab.name" :to="{ name: tab.name, params: { spaceId } }" :value="tab.name" exact>
      {{ t(tab.label) }}
    </v-tab>
  </v-tabs>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

const { t } = useI18n()
const route = useRoute()

const spaceId = computed(() => route.params.spaceId)

/** 设置的五栏，各是 `manage/settings` 下的一条子路由。 */
const tabs = [
  { name: 'SpacesDetailSettingsBasic', label: 'spaces.settings.tabs.basic' },
  { name: 'SpacesDetailSettingsCategories', label: 'spaces.settings.tabs.categories' },
  { name: 'SpacesDetailSettingsTemplates', label: 'spaces.settings.tabs.templates' },
  { name: 'SpacesDetailSettingsInviteCodes', label: 'spaces.settings.tabs.inviteCodes' },
  { name: 'SpacesDetailSettingsDomainGroups', label: 'spaces.settings.tabs.domainGroups' },
]
</script>

<style scoped lang="scss">
.settings-tabs {
  :deep(.v-slide-group__content) {
    gap: 6px;
  }

  :deep(.v-tab) {
    min-height: 44px;
    border-radius: var(--radius-md);
    letter-spacing: 0;
    text-transform: none;
  }
}
</style>
