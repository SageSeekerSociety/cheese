<script setup lang="ts">
// 首页这一层的外框。公开首页（未登录）自己占满整屏；其余几页（待办、团队与空间的
// 目录、空间列表、团队列表）放在首页侧栏旁边——手机上没有侧栏，它们各自就是整页。
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <router-view v-if="route.meta.publicLanding" />
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
