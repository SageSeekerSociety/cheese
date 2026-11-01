<script setup lang="ts">
import { failureNoticesHeld } from '@/lib/toast'

// 出错提示：从底部升起，过一会儿自己淡出。新的一条直接顶替旧的，不排队——排着的
// 旧提示说的多半是已经过去的事。页面被告知版本过期之后不再出现：那时每个请求都因
// 同一个原因被拒，「请刷新」那条提示是唯一该说的话（lib/toast.ts）。
defineProps<{
  message: string | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()
</script>

<template>
  <!-- 从底部升起，过一会儿自己淡出。新的一条直接顶替旧的，不排队：排着的旧提示
           说的多半是已经过去的事。 -->
  <Transition name="toast">
    <v-alert
      v-if="message && !failureNoticesHeld"
      :key="message"
      type="error"
      density="compact"
      class="chat-error-toast"
      closable
      @click:close="emit('close')"
    >
      {{ message }}
    </v-alert>
  </Transition>
</template>

<style scoped>
.chat-error-toast {
  position: absolute;
  left: 50%;
  bottom: 14px;
  transform: translateX(-50%);
  z-index: var(--z-shell);
  max-width: min(560px, calc(100% - 32px));
  overflow-wrap: anywhere;
  box-shadow: var(--shadow-2);
}
.toast-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.toast-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}
.toast-enter-from {
  opacity: 0;
  transform: translate(-50%, 8px);
}
.toast-leave-to {
  opacity: 0;
}
</style>
