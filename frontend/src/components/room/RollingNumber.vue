<script setup lang="ts">
// 一个会变的数字：变大时新数从下面滚上来，变小时从上面落下来，旧的往反方向让开。
// 房间里三处用它——同类事件的 ×N、表情的计数、新消息提示——说的是同一件事：
// 「多了一个」「少了一个」，而不是整行重画。
import { ref, watch } from 'vue'

const props = defineProps<{ value: number }>()

const direction = ref<'up' | 'down'>('up')
watch(
  () => props.value,
  (next, prev) => {
    direction.value = next >= prev ? 'up' : 'down'
  }
)
</script>

<template>
  <span class="roll">
    <Transition :name="`roll-${direction}`">
      <span :key="value" class="roll__digit">{{ value }}</span>
    </Transition>
  </span>
</template>

<style scoped>
/* 新旧两个数叠在同一格里交接，行宽不跟着跳。 */
.roll {
  display: inline-grid;
  overflow: hidden;
  vertical-align: bottom;
}
.roll__digit {
  grid-area: 1 / 1;
}
.roll-up-enter-active,
.roll-down-enter-active {
  transition:
    transform var(--dur-base) var(--ease-out),
    opacity var(--dur-base) var(--ease-out);
}
.roll-up-leave-active,
.roll-down-leave-active {
  transition:
    transform var(--dur-quick) var(--ease-in),
    opacity var(--dur-quick) var(--ease-in);
}
.roll-up-enter-from,
.roll-down-leave-to {
  opacity: 0;
  transform: translateY(60%);
}
.roll-up-leave-to,
.roll-down-enter-from {
  opacity: 0;
  transform: translateY(-60%);
}
</style>
