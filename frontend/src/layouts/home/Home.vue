<script setup lang="ts">
// 首页这一层的外框。公开首页（未登录）自己占满整屏；其余几页（待办、团队与空间的
// 目录、空间列表、团队列表）放在首页侧栏旁边——手机上没有侧栏，它们各自就是整页。
//
// 「现在是不是公开首页」从 useNavigation() 读：外框本身只看这一格 meta，不必为它
// 绑死 vue-router；没装路由的树里 nav 是 null，按「不是公开首页」画外框。
import { computed } from 'vue'

import { useNavigation } from '@/composables/useNavigation'

const nav = useNavigation()
const publicLanding = computed(() => Boolean(nav?.route?.meta.publicLanding))
</script>

<template>
  <router-view v-if="publicLanding" />
  <div v-else class="home-shell">
    <router-view />
  </div>
</template>

<style scoped>
/* 内容区照旧撑满：底下的页面（teams/Explore 那个满高页头）按百分比取高，
   父级不给一个真实高度它就塌成 auto。 */
.home-shell {
  height: 100%;
  width: 100%;
}
</style>
