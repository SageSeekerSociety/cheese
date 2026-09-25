<template>
  <!-- 手机上这一层不是抽屉，是页内分段（layouts/home/Home.vue）—— 这条侧栏
       只在桌面存在。 -->
  <SecondaryNavigation v-if="mdAndUp">
    <div class="sidebar-header">
      <span class="t-title">{{ t('navigation.home') }}</span>
      <v-spacer></v-spacer>
    </div>
    <v-list nav :lines="false" class="home-nav" bg-color="transparent">
      <!-- 我的工作在最上面：这一层里只有它是「我的东西」，也是登录后的落地页
           （router/home.ts 把 `/` 送到那儿）。 -->
      <v-list-item rounded="lg" prepend-icon="mdi-clipboard-text-outline" to="/work" :title="t('navigation.myWork')">
      </v-list-item>
      <v-list-item rounded="lg" prepend-icon="mdi-view-dashboard" to="/spaces" :title="t('navigation.spaces')">
      </v-list-item>
      <v-list-item rounded="lg" prepend-icon="mdi-account-group" to="/teams" :title="t('navigation.teams')">
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

<style scoped>
/* 选中与悬停都是中性色，同 TopicSidebar 的 .nav-row：这条侧栏坐在 --canvas 上，
   --fill 在那上面几乎看不见，所以悬停取 --fill-2、选中取 --line-2。琥珀在导航里
   只留给左栏那一格「当前在哪」。 */
.home-nav .v-list-item {
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.home-nav .v-list-item:hover {
  background: var(--fill-2);
}

.home-nav .v-list-item--active,
.home-nav .v-list-item--active:hover {
  background: var(--line-2);
}

.home-nav .v-list-item--active :deep(.v-list-item__overlay) {
  opacity: 0 !important;
}

.home-nav .v-list-item--active :deep(.v-list-item-title) {
  color: var(--ink);
  font-weight: 600;
}

.home-nav .v-list-item--active :deep(.v-icon) {
  color: var(--muted);
}
</style>
