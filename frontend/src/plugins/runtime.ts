// 应用开着的整段时间里一直要做、又和画哪一页无关的那几件事。main.ts 装一次；以前它们
// 写在 App.vue 里，根组件因此一导入就带着桌面 app、实时连接、全局按键这些只在浏览器里
// 才成立的东西，服务端预渲染没法直接渲染它。跑在应用的上下文里（runWithContext），
// 路由和仓库照常注入。主题不在这里：Vuetify 的主题只给组件用，跟随系统深浅色在 App.vue。
import type { App } from 'vue'
import type { Router } from 'vue-router'

import { effectScope, nextTick, watch } from 'vue'
import { useEventListener } from '@vueuse/core'

import { usePageTitle } from '@/composables/usePageTitle'

import { NotificationsApi } from '@/api/notifications'
import { installShortcuts, isTypingTarget } from '@/commands/shortcuts'
import { appShortcutSheetOpen } from '@/components/common/shortcutSheet'
import { offerToConnect } from '@/lib/desktop'
import { desktopListenForNotices, desktopStopNotices, onDesktopOpenPage } from '@/lib/desktopApp'
import { startInboxLive } from '@/lib/inboxLive'
import { trackKeyboardInset } from '@/lib/keyboardInset'
import AccountService from '@/services/account'
import { usePageTitleStore } from '@/stores/title'

export function installAppRuntime(app: App, router: Router): void {
  app.runWithContext(() =>
    effectScope(true).run(() => {
      // 软键盘盖住多少，写进 --keyboard-inset 供布局减掉 (style.css)。
      trackKeyboardInset()
      keepDocumentTitle(router)
      stayInTouchWhileSignedIn()
      // A clicked notification or the tray menu in the desktop app opens its page here.
      onDesktopOpenPage((path) => void router.push(path))
      listenForKeys(router)
    })
  )
}

function keepDocumentTitle(router: Router): void {
  const titleManager = usePageTitle()
  const store = usePageTitleStore()
  void router.isReady().then(() => {
    const updateDocumentTitle = () => {
      void nextTick(() => {
        document.title = titleManager.fullTitle.value
      })
    }
    watch(() => router.currentRoute.value.path, updateDocumentTitle, { immediate: true })
    watch(() => store.updateTrigger, updateDocumentTitle)
    watch(titleManager.fullTitle, updateDocumentTitle)
    watch(() => store.separator, updateDocumentTitle)
  })
}

/** 登录着的时候和服务端保持的那几条线：换了人就换线，退出就断。 */
function stayInTouchWhileSignedIn(): void {
  const signedInAs = () => AccountService.loggedIn && AccountService.user?.id

  // In the desktop app, signing in connects this computer as a device on its own —
  // a new one to the person's own team, one connected before back to its team
  // (lib/desktop.ts).
  watch(
    signedInAs,
    (userId) => {
      if (typeof userId === 'number') void offerToConnect(userId)
    },
    { immediate: true }
  )

  // The desktop app calls the person back while its window is closed: once handed
  // a credential it keeps its own connection for this account's notices.
  watch(
    signedInAs,
    (userId, previous) => {
      if (typeof userId === 'number') {
        desktopListenForNotices(userId, async () => (await NotificationsApi.liveToken()).token).catch(() => {})
      } else if (typeof previous === 'number') {
        desktopStopNotices()
      }
    },
    { immediate: true }
  )

  // 收件箱有新的一行时服务端当场说一声：「动态」和首页的未读小点据此去读（`lib/inboxLive.ts`）。
  let stopInboxLive: (() => void) | null = null
  watch(
    signedInAs,
    (userId) => {
      stopInboxLive?.()
      stopInboxLive =
        typeof userId === 'number' ? startInboxLive(async () => (await NotificationsApi.liveToken()).token) : null
    },
    { immediate: true }
  )
}

function listenForKeys(router: Router): void {
  installShortcuts(router)
  // `?` 打开快捷键表：焦点不在输入框里、不带修饰键（Shift 本身是打出 ? 的那一下）。门口页面
  // （官网）上没有应用外壳，这张表不在 DOM 里；后台有自己那张表（AdminLayout）。
  useEventListener(window, 'keydown', (event: KeyboardEvent) => {
    if (event.key !== '?' || event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return
    const route = router.currentRoute.value
    if (isTypingTarget(event.target) || route.meta.publicLanding || route.path.startsWith('/admin')) return
    event.preventDefault()
    appShortcutSheetOpen.value = true
  })
}
