<script setup lang="ts">
// 任务状态下面那一行的几样事实（待谁处理、说明、第几版、什么时候提交）：各成一格，靠间距
// 分开，不连成一句（`docs/design-system.md` §8）。一行放不下时最后一格截断；`wrap` 时换行。
defineProps<{ parts: readonly string[]; wrap?: boolean }>()
</script>

<template>
  <span class="state-facts" :class="{ 'state-facts--wrap': wrap }">
    <span v-for="(part, i) in parts" :key="i" class="state-facts__fact">{{ part }}</span>
  </span>
</template>

<style scoped>
.state-facts {
  display: flex;
  min-width: 0;
  gap: 0 10px;
  overflow: hidden;
}
.state-facts__fact {
  flex: none;
  white-space: nowrap;
}
.state-facts__fact:last-child {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.state-facts--wrap {
  flex-wrap: wrap;
  gap: 2px 10px;
}
.state-facts--wrap .state-facts__fact {
  flex: 0 1 auto;
  min-width: 0;
  white-space: normal;
}
</style>
