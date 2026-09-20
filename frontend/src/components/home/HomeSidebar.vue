<template>
  <!-- 手机上这一层不是抽屉，是页内分段（layouts/home/Home.vue）—— 这条侧栏
       只在桌面存在。 -->
  <SecondaryNavigation v-if="mdAndUp">
    <div class="sidebar-header">
      <span class="text-subtitle-1">{{ t('navigation.home') }}</span>
      <v-spacer></v-spacer>
    </div>
    <v-list nav :lines="false">
      <!-- 我的工作在最上面：这一层里只有它是「我的东西」，也是登录后的落地页
           （router/home.ts 把 `/` 送到那儿）。 -->
      <v-list-item
        rounded="lg"
        prepend-icon="mdi-clipboard-text-outline"
        to="/work"
        :title="t('navigation.myWork')"
        color="primary"
      >
      </v-list-item>
      <v-list-item
        rounded="lg"
        prepend-icon="mdi-view-dashboard"
        to="/spaces"
        :title="t('navigation.spaces')"
        color="primary"
      >
      </v-list-item>
      <v-list-item
        rounded="lg"
        prepend-icon="mdi-account-group"
        to="/teams"
        :title="t('navigation.teams')"
        color="primary"
      >
      </v-list-item>
    </v-list>
  </SecondaryNavigation>
</template>

<script setup lang="ts">
import { useDisplay } from 'vuetify'

import SecondaryNavigation from '@/components/common/Navigation/SecondaryNavigation.vue'
import { t } from '@/i18n'

const { mdAndUp } = useDisplay()
</script>
