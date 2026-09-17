<!--
  有新版本时顶上那条提示。

  它替代的是「悄悄刷新页面」：新 service worker 装好之后停在 waiting，用户点
  「立即更新」才接管并刷新（pwa.ts）。所以这条横幅不是一个装饰，它是更新唯一的
  出口——不出现的话，这个页面会一直跑着旧版本。

  「稍后」只收起这一次：新 worker 还在 waiting，下次打开页面会再报。所以它不会
  变成「关一次就永远收不到更新」的按钮。

  离线时不显示：顶上只有一个位置（OfflineBanner 占着），而没网是更急的那件事，
  更新等有网再说。
-->
<script setup lang="ts">
import { useOnline } from '@vueuse/core'

import { applyUpdate, dismissUpdate, updateReady } from '@/pwa'

const online = useOnline()
</script>

<template>
  <Transition name="update-slide">
    <div v-if="updateReady && online" class="update-banner" role="status" aria-live="polite">
      <v-icon size="16" class="update-banner__icon">mdi-download</v-icon>
      <span>芝士有新版本，更新后自动刷新</span>
      <button type="button" class="update-banner__action update-banner__action--primary" @click="applyUpdate">
        立即更新
      </button>
      <button type="button" class="update-banner__action" @click="dismissUpdate">稍后</button>
    </div>
  </Transition>
</template>

<style scoped>
.update-banner {
  position: fixed;
  top: 0;
  left: 50%;
  z-index: 3000; /* 和离线横幅同一层：两者不会同时出现 */
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 6px 14px;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
  /* 现状色横幅，前景色跟着底色翻面（同 OfflineBanner 的理由）：primary 在浅色
     下是琥珀、深色下提亮，写死的白字在其中一套上会糊掉。 */
  color: rgb(var(--v-theme-on-primary));
  white-space: nowrap;
  background: rgb(var(--v-theme-primary));
  /* 只圆下面两角：横幅贴着屏幕顶边，上面两角必须方。写成 longhand 是因为
     stylelint 的圆角阶梯只认单值（`0 0 x x` 这种简写一律判越界）。 */
  border-radius: 0;
  border-bottom-left-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  box-shadow: var(--shadow-1);
  transform: translateX(-50%);
}
.update-banner__icon {
  color: rgb(var(--v-theme-on-primary));
}
.update-banner__action {
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 600;
  color: inherit;
  cursor: pointer;
  background: transparent;
  border: 1px solid currentcolor;
  border-radius: 999px;
  opacity: 0.85;
}
.update-banner__action:hover {
  opacity: 1;
}
.update-banner__action--primary {
  /* 主操作是实心的：它和「稍后」在同一条横幅上，两者的差别要一眼看得出。 */
  color: rgb(var(--v-theme-primary));
  background: rgb(var(--v-theme-on-primary));
  border-color: transparent;
}
.update-slide-enter-active,
.update-slide-leave-active {
  transition:
    transform 0.22s ease,
    opacity 0.22s ease;
}
.update-slide-enter-from,
.update-slide-leave-to {
  opacity: 0;
  transform: translate(-50%, -100%);
}
</style>
