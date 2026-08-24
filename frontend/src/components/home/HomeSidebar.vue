<template>
  <!-- 手机上这一层不是抽屉，是页内分段（layouts/home/Home.vue）—— 这条侧栏
       只在桌面存在。 -->
  <SecondaryNavigation v-if="mdAndUp">
    <div class="sidebar-header">
      <span class="text-subtitle-1">首页</span>
      <v-spacer></v-spacer>
    </div>
    <v-list nav :lines="false">
      <v-list-item rounded="lg" prepend-icon="mdi-view-dashboard" to="/spaces" title="空间" color="primary">
      </v-list-item>
      <v-list-item rounded="lg" prepend-icon="mdi-account-group" to="/teams" title="小队" color="primary">
      </v-list-item>
    </v-list>
  </SecondaryNavigation>
</template>

<script setup lang="ts">
import { useDisplay } from 'vuetify'

import SecondaryNavigation from '@/components/common/Navigation/SecondaryNavigation.vue'

const { mdAndUp } = useDisplay()
</script>
