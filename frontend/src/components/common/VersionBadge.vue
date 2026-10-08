<!--
  内测版本徽标: a small fixed badge showing which backend build is running, so a
  tester can confirm their version at a glance. Renders nothing unless the box
  opted in (GET /api/version → badge:true), so it's inert in prod. Click to copy
  the full sha. The build arrives as a prop: the shell (App.vue) asks the server,
  this only draws the answer, so it renders with no backend at all.
-->
<template>
  <div
    v-if="version?.badge && version.short"
    class="version-badge"
    :title="t('shell.version.title', { sha: version.sha })"
    @click="copySha"
  >
    <span class="version-badge__dot" />
    {{ copied ? t('navigation.copy.done') : version.short }}
  </div>
</template>

<script setup lang="ts">
import type { AppVersion } from '@/api'

import { ref } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{ version: AppVersion | null }>()
const copied = ref(false)

async function copySha() {
  if (!props.version?.sha) return
  try {
    await navigator.clipboard.writeText(props.version.sha)
    copied.value = true
    window.setTimeout(() => (copied.value = false), 1200)
  } catch {
    // clipboard blocked — the title tooltip still shows the full sha
  }
}
</script>

<style scoped>
.version-badge {
  position: fixed;
  /* 钉在右上角：iPhone 上正好落进刘海 / 状态栏那一条，字被压住。让出顶部与右侧安全区
     （横屏时圆角 / 刘海在侧边）。桌面和没有安全区的设备上 `env()` 是 0，位置不变。 */
  top: calc(6px + env(safe-area-inset-top, 0px));
  right: calc(8px + env(safe-area-inset-right, 0px));
  z-index: var(--z-banner);
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  line-height: 1.4;
  /* 「内测版本」是一个提示性状态，按 §1.5 的状态三件套走：文字用能读的 --warn-ink，
     底色用 --warn-wash，边框/圆点用记号色 --warn。原来的三个琥珀字面量在深色下不变，
     会变成浅底深字压在深色页面上。 */
  color: var(--warn-ink);
  background: var(--warn-wash);
  border: 1px solid var(--warn);
  border-radius: 999px;
  cursor: pointer;
  user-select: none;
  pointer-events: auto;
}
.version-badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--warn);
}
</style>
