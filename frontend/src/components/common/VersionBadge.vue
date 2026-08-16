<!--
  内测版本徽标: a small fixed badge showing which backend build is running, so a
  tester can confirm their version at a glance. Renders nothing unless the box
  opted in (GET /api/version → badge:true), so it's inert in prod. Click to copy
  the full sha.
-->
<template>
  <div
    v-if="version?.badge && version.short"
    class="version-badge"
    :title="`部署版本 ${version.sha}（点击复制）`"
    @click="copySha"
  >
    <span class="version-badge__dot" />
    {{ copied ? '已复制' : version.short }}
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { type AppVersion, getAppVersion } from '@/api'

const version = ref<AppVersion | null>(null)
const copied = ref(false)

onMounted(async () => {
  try {
    version.value = await getAppVersion()
  } catch {
    // Best-effort: no badge if the endpoint is unreachable or old.
  }
})

async function copySha() {
  if (!version.value?.sha) return
  try {
    await navigator.clipboard.writeText(version.value.sha)
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
  top: 6px;
  right: 8px;
  z-index: 3000;
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
