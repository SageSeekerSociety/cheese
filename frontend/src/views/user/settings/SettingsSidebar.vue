<template>
  <v-navigation-drawer permanent class="page-sidebar border-e-0 border-b-0" border="sm" color="background">
    <div class="sidebar-header">
      <v-avatar size="24" :image="getAvatarUrl(userData?.avatarId)" />
      <span class="text-subtitle-1">{{ userData?.nickname }}</span>
      <v-spacer></v-spacer>
    </div>
    <v-list nav bg-color="transparent" rounded="lg" color="primary">
      <v-list-item v-for="tab in tabs" :key="tab.route.name" rounded="lg" :value="tab.route.name" :to="tab.route">
        <template v-if="tab.icon" #prepend>
          <v-icon>{{ tab.icon }}</v-icon>
        </template>
        <v-list-item-title>{{ tab.label() }}</v-list-item-title>
      </v-list-item>
    </v-list>
  </v-navigation-drawer>
</template>

<script lang="ts" setup>
import { getAvatarUrl } from '@/utils/materials'

import { t } from '@/i18n'
import AccountService from '@/services/account'

const userData = AccountService._user

const tabs = [
  { label: () => t('account.profile.title'), route: { name: 'UserSettingsProfile' }, icon: 'mdi-account' },
  { label: () => t('account.settings.realName'), route: { name: 'UserSettingsRealName' }, icon: 'mdi-account-card' },
  { label: () => t('account.security.title'), route: { name: 'UserSettingsSecurity' }, icon: 'mdi-lock' },
  { label: () => t('account.settings.install'), route: { name: 'UserSettingsApp' }, icon: 'mdi-cellphone-arrow-down' },
]
</script>
