<script setup lang="ts">
// 手机底栏「首页」那一格：团队和空间的目录，和桌面首页侧栏是同一份（HomeNav）。
// 桌面上这份目录常驻在侧栏里，这一页就没有必要存在，直接去待办。
import { watchEffect } from 'vue'
import { useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import HomeNav from './HomeNav.vue'

const { mdAndUp } = useDisplay()
const router = useRouter()

watchEffect(() => {
  if (mdAndUp.value) void router.replace({ name: 'inbox' })
})
</script>

<template>
  <div class="home-hub">
    <HomeNav />
  </div>
</template>

<style scoped>
.home-hub {
  height: 100%;
  overflow-y: auto;
  padding: 8px 8px 96px;
}
</style>
