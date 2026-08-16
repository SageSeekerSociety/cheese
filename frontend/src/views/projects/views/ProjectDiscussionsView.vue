<template>
  <div class="pa-4 pa-sm-6 text-center py-8 py-md-12">
    <v-icon icon="mdi-forum-outline" size="64" class="mb-4 empty-state-icon"></v-icon>
    <h3 class="text-h6 font-weight-medium mb-2">讨论区功能开发中</h3>
    <p class="text-body-2 text-medium-emphasis mb-4">在这里，小组成员可以交流和记录项目进度，类似 GitHub Discussion</p>
    <p class="text-body-2 text-medium-emphasis">每个讨论支持回复和表情反应，便于项目协作沟通</p>
  </div>
</template>

<script setup lang="ts">
// 暂无需要的脚本逻辑
</script>

<style scoped>
/* 空状态的占位图标 —— design-system §1.3 的 --faint 档（“占位提示”）。
   原值 grey-lighten-2 恒等于 #EEEEEE：浅色下几乎隐形，深色下是一块刺眼亮斑。 */
.empty-state-icon {
  color: var(--faint);
}
</style>
