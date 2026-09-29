<template>
  <!-- 手机上没有常驻侧栏：同一份清单在内容上方排成一行标签（layouts/user/Settings.vue）。 -->
  <v-navigation-drawer
    v-if="mdAndUp"
    permanent
    class="page-sidebar border-e-0 border-b-0"
    border="sm"
    color="background"
  >
    <div class="sidebar-header">
      <v-avatar size="24" :image="getAvatarUrl(userData?.avatarId)" />
      <span class="text-subtitle-1">{{ userData?.nickname }}</span>
      <v-spacer></v-spacer>
    </div>
    <v-list nav bg-color="transparent" rounded="lg" color="primary">
      <v-list-item
        v-for="tab in SETTINGS_SECTIONS"
        :key="tab.route.name"
        rounded="lg"
        :value="tab.route.name"
        :to="tab.route"
      >
        <template v-if="tab.icon" #prepend>
          <v-icon>{{ tab.icon }}</v-icon>
        </template>
        <v-list-item-title>{{ tab.label() }}</v-list-item-title>
      </v-list-item>
    </v-list>
  </v-navigation-drawer>
</template>

<script lang="ts" setup>
import { useDisplay } from 'vuetify'

import { getAvatarUrl } from '@/utils/materials'

import { SETTINGS_SECTIONS } from './sections'

import AccountService from '@/services/account'

const { mdAndUp } = useDisplay()
const userData = AccountService._user
</script>
