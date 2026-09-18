<template>
  <v-navigation-drawer permanent class="page-sidebar border-e-0 border-b-0" border="sm" color="background">
    <div class="sidebar-header">
      <v-avatar size="24" :image="getAvatarUrl(userData?.avatarId)" />
      <span class="text-subtitle-1">{{ userData?.nickname }}</span>
      <v-spacer></v-spacer>
    </div>
    <v-list nav bg-color="transparent" rounded="lg" color="primary">
      <v-list-item v-for="tab in tabs" :key="tab.label" rounded="lg" :value="tab.route.name" :to="tab.route">
        <template v-if="tab.icon" #prepend>
          <v-icon>{{ tab.icon }}</v-icon>
        </template>
        <v-list-item-title>{{ tab.label }}</v-list-item-title>
      </v-list-item>
    </v-list>
  </v-navigation-drawer>
</template>

<script lang="ts" setup>
import type { User } from '@/types/users'

import { computed, inject } from 'vue'
import { useI18n } from 'vue-i18n'

import { getAvatarUrl } from '@/utils/materials'

import AccountService from '@/services/account'

const { t } = useI18n()
const userData = AccountService._user

// 写成 computed 而不是常量：切语言时标签要跟着变。
const tabs = computed(() => [
  {
    label: t('users.settings.sidebar.profile'),
    route: {
      name: 'UserSettingsProfile',
    },
    icon: 'mdi-account',
  },
  {
    label: t('users.settings.sidebar.realName'),
    route: {
      name: 'UserSettingsRealName',
    },
    icon: 'mdi-account-card',
  },
  {
    label: t('users.settings.sidebar.privacy'),
    route: {
      name: 'UserPrivacyCenter',
    },
    icon: 'mdi-shield-lock',
  },
  {
    label: t('users.settings.sidebar.security'),
    route: {
      name: 'UserSettingsSecurity',
    },
    icon: 'mdi-lock',
  },
  {
    label: t('users.settings.sidebar.app'),
    route: {
      name: 'UserSettingsApp',
    },
    icon: 'mdi-cellphone-arrow-down',
  },
])
</script>
