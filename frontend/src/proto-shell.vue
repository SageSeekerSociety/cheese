<script setup lang="ts">
// 反馈原型精简构建用的外壳（临时，供话题预览用）。
//
// 不套 App.vue：那棵子树会把代码编辑器（monaco，含 7MB 的 ts.worker）、导航栏、
// 工作区面板一起拉进产物——这些都是反馈中心用不到的东西，而它们在预览通道上要
// 按秒计地等人下载。反馈中心页自己就是「独立完整页面」（见页面顶部注释：不套
// 左侧话题导航），所以这里只补它需要的三层容器，类名与 App.vue 里那一份一致，
// 页面内的滚动、背景、边框行为不变。
</script>

<template>
  <v-app class="bg-background h-100">
    <v-main class="bg-background h-100">
      <div class="border-t-sm bg-background h-100 overflow-hidden">
        <div id="app-scrollable" class="app-content h-100">
          <router-view />
        </div>
      </div>
    </v-main>
  </v-app>
</template>
