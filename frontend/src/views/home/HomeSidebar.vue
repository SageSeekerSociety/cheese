<template>
  <!-- 手机上没有常驻侧栏：同一份目录是底栏「首页」那一格的整页（HomeHub）。 -->
  <SecondaryNavigation v-if="mdAndUp">
    <div class="sidebar-header">
      <span class="t-title">{{ t('navigation.home') }}</span>
      <v-spacer></v-spacer>
    </div>
    <HomeNav inbox />
  </SecondaryNavigation>
</template>

<script setup lang="ts">
import { useDisplay } from 'vuetify'

import HomeNav from './HomeNav.vue'

import SecondaryNavigation from '@/components/common/Navigation/SecondaryNavigation.vue'
import { t } from '@/i18n'

const { mdAndUp } = useDisplay()
</script>
