<script setup lang="ts">
// 「来了几条新的」那颗药丸：浮在时间线底部，从下面升上来。点它回到最新。
// 往上翻着的时候它同时在数数和提示位置；停在历史中间时它写「回到最新」——
// 那时候底部只是这一段的底部。
import RollingNumber from '../room/RollingNumber.vue'

import { t } from '@/i18n'

defineProps<{
  count: number
  hasNewer: boolean
}>()

const emit = defineEmits<{
  (e: 'jump'): void
}>()
</script>

<template>
  <!-- 往上翻着的时候来了新消息。 -->
  <div class="new-pill-anchor">
    <Transition name="new-pill">
      <button v-if="count || hasNewer" type="button" class="new-pill" @click="emit('jump')">
        <v-icon size="14">mdi-arrow-down</v-icon>
        <template v-if="count">
          <RollingNumber :value="count" />
          <span>{{ t('work.room.newMessages') }}</span>
        </template>
        <span v-else>{{ t('work.room.backToLatest') }}</span>
      </button>
    </Transition>
  </div>
</template>

<style scoped>
/* 新消息提示：浮在时间线底部正中，从下面升上来。 */
.new-pill-anchor {
  position: relative;
  height: 0;
}
.new-pill {
  position: absolute;
  bottom: 12px;
  left: 50%;
  z-index: var(--z-raised-5);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 12px;
  border-radius: var(--radius-pill);
  background: var(--accent);
  color: rgb(var(--v-theme-on-primary)); /* 和发送键同一对：琥珀底上的字 */
  font-size: 13px;
  font-weight: 600;
  box-shadow: var(--shadow-2);
  transform: translateX(-50%);
  cursor: pointer;
}
/* 触屏上手指点得中：药丸画出来还是 28px 高，能点的范围撑到 44px（已有定位）。 */
@media (pointer: coarse) {
  .new-pill::before {
    content: '';
    position: absolute;
    top: 50%;
    left: 50%;
    width: max(100%, 44px);
    height: max(100%, 44px);
    transform: translate(-50%, -50%);
  }
}
.new-pill-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.new-pill-leave-active {
  transition:
    opacity var(--dur-quick) var(--ease-in),
    transform var(--dur-quick) var(--ease-in);
}
.new-pill-enter-from,
.new-pill-leave-to {
  opacity: 0;
  transform: translate(-50%, 8px);
}
</style>
