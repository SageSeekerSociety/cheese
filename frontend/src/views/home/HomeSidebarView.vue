<script setup lang="ts">
// 桌面首页侧栏的外壳：全站那只抽屉（`SecondaryNavigation`）加一行「首页」标题。
// 目录本身从默认槽进来 —— 这一只不认识团队和空间，也不去取任何东西。
//
// 手机上没有常驻侧栏：同一份目录是底栏「首页」那一格的整页（HomeHub）。
import { useDisplay } from 'vuetify'

import SecondaryNavigation from '@/components/common/Navigation/SecondaryNavigation.vue'
import { t } from '@/i18n'

const { mdAndUp } = useDisplay()
</script>

<template>
  <SecondaryNavigation v-if="mdAndUp">
    <div class="sidebar-header">
      <span class="t-title">{{ t('navigation.home') }}</span>
      <v-spacer></v-spacer>
    </div>
    <slot />
  </SecondaryNavigation>
</template>
