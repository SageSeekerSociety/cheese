<script setup lang="ts">
// 任务状态下面那一行的几样事实（待谁处理、说明、第几版、什么时候提交）：各成一格，靠间距
// 分开，不连成一句（`docs/design-system.md` §8）。一行放不下时从末尾截断；`wrap` 时换行。
defineProps<{ parts: readonly string[]; wrap?: boolean }>()
</script>

<template>
  <span class="state-facts" :class="{ 'state-facts--wrap': wrap }">
    <span v-for="(part, i) in parts" :key="i" class="state-facts__fact">{{ part }}</span>
  </span>
</template>

<style scoped>
/* 一行：几格照先后排，放不下时从末尾截断，先说的先留。 */
.state-facts {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.state-facts__fact + .state-facts__fact {
  margin-left: 10px;
}
.state-facts--wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 10px;
  overflow: visible;
  white-space: normal;
}
.state-facts--wrap .state-facts__fact {
  min-width: 0;
  margin-left: 0;
}
</style>
