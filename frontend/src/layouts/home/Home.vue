<script setup lang="ts">
// 首页这一层（空间 / 小队）在两端是两种形态。
//
// 桌面上它是左边那条常驻侧栏（HomeSidebar）。手机上没有常驻二级侧栏
// （docs/plans/2026-08-18-mobile-shell-design.md §3.3）—— 底栏已经是一层常驻
// chrome，再叠一个抽屉就要求人记住「哪些东西在下面、哪些在汉堡里」，而这个划分
// 没有语义依据。所以同一份清单在手机上压成页内分段，跟着内容走。
//
// 是分段而不是页面栈，因为空间和小队是**并列**的：它们之间没有上下级，
// 所以这一层不给 ←、也不收底栏。
import { useDisplay } from 'vuetify'

const { mdAndUp } = useDisplay()
</script>

<template>
  <div class="home-shell">
    <v-tabs v-if="!mdAndUp" class="home-sections" grow slider-color="primary" bg-color="transparent">
      <v-tab :to="{ name: 'HomeSpaces' }">空间</v-tab>
      <v-tab :to="{ name: 'HomeTeams' }">小队</v-tab>
    </v-tabs>
    <div class="home-shell__body">
      <router-view />
    </div>
  </div>
</template>

<style scoped>
.home-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.home-sections {
  flex: 0 0 auto;
  border-bottom: var(--app-page-header-rule);
}
/* 内容区照旧撑满剩下的高度：底下的页面（teams/Explore 的那个满高页头）按
   百分比取高，父级不给一个真实高度它就塌成 auto。 */
.home-shell__body {
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
}
</style>
