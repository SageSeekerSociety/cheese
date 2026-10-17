<template>
  <!-- The "can't confirm your session, retry" layer for a cold open. It
       comes first because it has to cover both branches — the shell and the public
       page: on a weak network, someone whose session was still good used to be
       sent to the marketing page. See RestorePhase in services/account.ts. -->
  <SessionRestoreGate
    :visible="restoreVisible"
    :retrying="restoreRetrying"
    :navigation-failed="restoreNavigationFailed"
    @retry="retryRestore"
    @continue="continueAsGuest"
  />
  <!-- The public pages (home, about, solutions) are documents of their own; every
       other page sits in the app's frame (layouts/AppShell.vue). -->
  <router-view v-if="currentRoute.meta.publicLanding" />
  <AppShell v-else />
</template>

<script setup lang="ts">
// 根组件：全局要注入的几样、主题跟随、登录恢复那一层，再在门口页面和应用外框之间选一个。
// 页面画什么在路由和外框里；和画什么无关、一直在跑的那几件由 main.ts 装（plugins/runtime.ts）。
import { watch } from 'vue'
import { useRoute } from 'vue-router'

import { provideAttachmentSource } from '@/composables/useAttachmentSource'
import { providePageChrome } from '@/composables/usePageChrome'
import { providePublicVisitor } from '@/composables/usePublicVisitor'
import { useSessionRestore } from '@/composables/useSessionRestore'
import { provideUserRefDirectory } from '@/composables/useUserRefDirectory'

import SessionRestoreGate from '@/components/common/SessionRestoreGate.vue'
import AppShell from '@/layouts/AppShell.vue'
import { tellDesktopTheme } from '@/lib/desktopApp'
import { useAppTheme } from '@/theme'

// Activate the theme runtime app-wide. First paint is already correct without
// this (the boot script in index.html stamps <html data-theme>, and Vuetify
// boots on the same resolved value), but the OS-preference LISTENER lives in
// this composable — and the only other caller, ThemeToggle, sits inside the
// logged-in user menu. Without this line a signed-out visitor sitting on the
// login page would not follow their machine switching to dark at sunset.
//
// Not while prerendering a public page at build time (scripts/prerender.mjs): there
// is no document to stamp and no OS to follow, and the page is rendered in both
// themes' variables already.
if (!import.meta.env.SSR) {
  const appTheme = useAppTheme()
  // The desktop app paints its first page and its title bar in the same theme.
  watch(appTheme.preference, tellDesktopTheme, { immediate: true })
}

const currentRoute = useRoute()

// 外壳给内容区注入的四样东西：人名 chip 的名册（lib/userRefDirectory.ts）、页头的
// 面包屑与操作区（lib/pageChrome.ts）、公共页要的登录状态（lib/publicVisitor.ts）、
// 附件字节从哪儿取（lib/attachmentSource.ts）。组件只认注入口，没人注入时各有兜底，
// 所以它们能离开外壳单独挂起来。
provideUserRefDirectory()
providePageChrome()
providePublicVisitor()
provideAttachmentSource()

// 冷打开时「连不上、可以重试」那一层。逻辑在 composable 里
// ——组件边界规则不允许子组件碰 services/router，而这里是视图那一层。
const {
  visible: restoreVisible,
  retrying: restoreRetrying,
  navigationFailed: restoreNavigationFailed,
  retry: retryRestore,
  continueAsGuest,
} = useSessionRestore()
</script>
