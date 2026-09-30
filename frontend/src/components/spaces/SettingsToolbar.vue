<script setup lang="ts">
// 设置页一栏里的那一行：左边是这一块叫什么（一栏里只有一块时不写），右边是这一块
// 的操作。页名和分栏在页头上，这一行不再写一遍。
defineProps<{ title?: string }>()
</script>

<template>
  <div class="settings-toolbar">
    <h2 v-if="title" class="settings-toolbar__title t-title">{{ title }}</h2>
    <div class="settings-toolbar__actions"><slot /></div>
  </div>
</template>

<style scoped>
.settings-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 44px;
  margin-bottom: 12px;
}

.settings-toolbar__title {
  margin: 0;
  color: var(--ink);
}

.settings-toolbar__actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
</style>
