<script setup lang="ts">
// A slim top banner shown while the browser is offline. The offline story
// (owner spec) is: the shell + already-cached content stay usable, but new
// messages / notifications / live updates cannot arrive — so tell the user
// plainly rather than let a frozen pane read as a bug. It self-hides the moment
// the network returns; the WS panels (ChatPanel / DeviceLiveViewer) reconnect
// and refetch on that same `online` edge, so "有网就自动转出来".
//
// `useOnline` tracks navigator.onLine + the window online/offline events.
import { useOnline } from '@vueuse/core'

const online = useOnline()
</script>

<template>
  <Transition name="offline-slide">
    <div v-if="!online" class="offline-banner" role="status" aria-live="polite">
      <v-icon size="16" class="offline-banner__icon">mdi-wifi-off</v-icon>
      <span>离线中 · 无法收发新消息，恢复网络后会自动重连</span>
    </div>
  </Transition>
</template>

<style scoped>
.offline-banner {
  position: fixed;
  top: 0;
  left: 50%;
  z-index: 3000; /* above the app bar and drawers */
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 6px 16px;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
  /* 实心的警告色横幅，前景色必须跟着底色翻面：浅色下 --warn 是 #E8901C，白字；深色下
     它提亮到 #F0A94A，白字只剩 1.7:1。on-warning 是 Vuetify 按主题各自推导出来的
     前景色（浅色 #fff、深色 #000），只在 Vuetify 那一侧存在，所以这里写 --v-theme-*
     而不是自研 token —— 见 docs/design-system.md §5。 */
  color: rgb(var(--v-theme-on-warning));
  white-space: nowrap;
  background: rgb(var(--v-theme-warning));
  border-radius: 0 0 10px 10px;
  box-shadow: var(--shadow-1);
  transform: translateX(-50%);
}
.offline-banner__icon {
  color: rgb(var(--v-theme-on-warning));
}
.offline-slide-enter-active,
.offline-slide-leave-active {
  transition:
    transform 0.22s ease,
    opacity 0.22s ease;
}
.offline-slide-enter-from,
.offline-slide-leave-to {
  opacity: 0;
  transform: translate(-50%, -100%);
}
</style>
