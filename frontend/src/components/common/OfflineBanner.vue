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
  color: #fff;
  white-space: nowrap;
  background: #e8901c; /* --warn (Vuetify `warning`) */
  border-radius: 0 0 10px 10px;
  box-shadow: 0 2px 8px rgb(0 0 0 / 18%);
  transform: translateX(-50%);
}
.offline-banner__icon {
  color: #fff;
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
