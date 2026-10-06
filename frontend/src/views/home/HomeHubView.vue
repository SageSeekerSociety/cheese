<script setup lang="ts">
// 手机底栏「首页」那一格的壳：一块自己滚的整页。目录从默认槽进来 —— 这一只不认识团队
// 和空间，也不去取任何东西。
//
// 桌面上这份目录常驻在侧栏里（HomeSidebar），这一页在桌面上没有存在的必要：什么时候
// 把人送回待办，是页面容器的事。
</script>

<template>
  <div class="home-hub">
    <slot />
  </div>
</template>

<style scoped>
.home-hub {
  height: 100%;
  overflow-y: auto;
  padding: 8px 8px 96px;
}
</style>
