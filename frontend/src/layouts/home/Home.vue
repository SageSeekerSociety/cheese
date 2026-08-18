<script setup lang="ts">
// 首页这一层（空间 / 小队）在两端是两种形态。
//
// 桌面上它是左边那条常驻侧栏（HomeSidebar）。手机上没有常驻二级侧栏
// （docs/plans/2026-08-18-mobile-shell-design.md §3.3）—— 底栏已经是一层常驻
// chrome，再叠一个抽屉就要求人记住「哪些东西在下面、哪些在汉堡里」，而这个划分
// 没有语义依据。所以同一份清单在手机上压成两格分段。
//
// 分段住在**顶栏里**，不在页面上：放页面上的话，小队那一页会出现两行 tab
// （这一对 + 它自己的 发现/我的/待定），而且顶栏还得再写一遍这一层的名字。
// 是分段而不是页面栈，因为空间和小队是并列的：没有上下级，所以不给 ←、
// 也不收底栏。
import { useDisplay } from 'vuetify'

const { mdAndUp } = useDisplay()
</script>

<template>
  <Teleport v-if="!mdAndUp" to="#app-bar-slot">
    <v-tabs class="home-sections" grow slider-color="primary" bg-color="transparent" height="56">
      <v-tab :to="{ name: 'HomeSpaces' }">空间</v-tab>
      <v-tab :to="{ name: 'HomeTeams' }">小队</v-tab>
    </v-tabs>
  </Teleport>
  <div class="home-shell">
    <router-view />
  </div>
</template>

<style scoped>
.home-sections {
  flex: 1 1 auto;
  min-width: 0;
}
/* 内容区照旧撑满：底下的页面（teams/Explore 那个满高页头）按百分比取高，
   父级不给一个真实高度它就塌成 auto。 */
.home-shell {
  height: 100%;
  width: 100%;
}
</style>
