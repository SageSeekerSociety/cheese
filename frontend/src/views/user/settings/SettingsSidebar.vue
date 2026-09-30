<template>
  <!-- 手机上没有常驻侧栏：同一份清单在内容上方排成一行标签（layouts/user/Settings.vue）。 -->
  <SecondaryNavigation v-if="mdAndUp">
    <div class="sidebar-header settings-head">
      <v-avatar size="20" :image="getAvatarUrl(userData?.avatarId)" />
      <span class="settings-head__name t-title">{{ userData?.nickname }}</span>
    </div>
    <v-list nav density="compact" :lines="false" class="side-nav pa-2" bg-color="transparent">
      <v-list-item
        v-for="tab in SETTINGS_SECTIONS"
        :key="tab.route.name"
        rounded="lg"
        :value="tab.route.name"
        :to="tab.route"
        :prepend-icon="tab.icon"
        :title="tab.label()"
      />
    </v-list>
  </SecondaryNavigation>
</template>

<script lang="ts" setup>
import { useDisplay } from 'vuetify'

import { getAvatarUrl } from '@/utils/materials'

import { SETTINGS_SECTIONS } from './sections'

import SecondaryNavigation from '@/components/common/Navigation/SecondaryNavigation.vue'
import AccountService from '@/services/account'

const { mdAndUp } = useDisplay()
const userData = AccountService._user
</script>

<style scoped>
.settings-head {
  justify-content: flex-start;
}

.settings-head__name {
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
